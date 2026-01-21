from odoo import models, fields, api
import requests
import json
from datetime import datetime
from requests.adapters import HTTPAdapter
from odoo.tools import config
from urllib3.util.retry import Retry
import logging
_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Campo temporal para almacenar la selección (opcional, para transacciones)
    selected_warehouse_id = fields.Many2one(
        'warehouse.list',
        string='Almacén Seleccionado Temporal',
        copy=False
    )

    warehouse_selection = fields.Selection(
        selection='_get_warehouse_selection',
        string='Seleccionar Almacén'
    )
    
    warehouse_count = fields.Integer(
        compute='_compute_warehouse_count'
    )

    def send_delivery_to_laravel(self,warehouse_id):
        """Envía la entrega a Place Vendor vía GraphQL"""
        for order in self:
            if not hasattr(order, 'picking_ids') or not order.picking_ids:
                return self._notify('Error', 'No hay entregas para esta orden o módulo sale_stock no instalado')

            errors = []
            for picking in order.picking_ids:
                try:
                    self._send_graphql_mutation(picking, order,warehouse_id)
                except Exception as e:
                    errors.append(f"{picking.name}: {str(e)}")

            if errors:
                return self._notify('Error enviando a Place Vendor', '\n'.join(errors))

       # return self._notify('Éxito', 'Entrega(s) enviada(s) a Place Vendor')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Éxito',
                'message': 'Entrega(s) enviada(s) a Place Vendor',
                'sticky': False,
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'}
            }
        }

    def _send_graphql_mutation(self, picking, order,warehouse_id):

        auth_config =  self._autenticacion_placevendor()

        if not hasattr(auth_config, 'is_authenticated') or not auth_config.is_authenticated:
            return self._notify('Error', "No estás autenticado en Place Vendor")
        
        laravel_url = auth_config['laravel_url']
        laravel_email = auth_config['laravel_user']
        laravel_password = auth_config['laravel_password']

        # Configurar sesión
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(['POST'])
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        try:
            _logger.info(f"\n{'='*60}")
            _logger.info(f"DEBUG - Enviando entrega: {picking.name}")
            _logger.info(f"DEBUG - URL: {laravel_url}")
            _logger.info(f"{'='*60}")

            #  OBTENER LOS PRODUCTOS DE LA ORDEN
            product_line = self._prepare_product_line(order)
            _logger.info(f"DEBUG - Productos a enviar: {len(product_line)}")
            
            #  PREPARAR LAS VARIABLES PARA LA ENTREGA
            scheduled_date = picking.scheduled_date or datetime.now()
            
            # Formato ISO 8601 para Place Vendor
            date_str = scheduled_date.strftime('%Y-%m-%d %H:%M:%S')
            
            # Obtener dirección
            address_delivery = ""
            try:
                if picking.partner_id:
                    addr = picking.partner_id._display_address()
                    address_delivery = (addr or '').strip().replace('\n', ', ')
            except:
                pass
            
            if not address_delivery:
                try:
                    if order.partner_id:
                        addr = order.partner_id._display_address()
                        address_delivery = (addr or '').strip().replace('\n', ', ')
                except:
                    pass
            
            if not address_delivery:
                address_delivery = "Dirección no especificada"
            
            # Información del cliente
            cliente_info = self._prepare_contact_info(order.partner_id, 'Cliente')
            """ cliente_info = {
                'name': order.partner_id.name or '',
                'email': order.partner_id.email or '',
                'phone': order.partner_id.phone or ''
            } """

            # Información del responsable
            responsable_info = self._prepare_contact_info(order.user_id, 'Responsable')
            """ responsable_info = {
                'name': order.user_id.name or '',
                'email': order.user_id.email or ''
            } """
            
            doc_origin = picking.name or f"PICK-{picking.id}"
            firma = order.name or f"SO-{order.id}"
            
            # Variables para la mutación de entrega
            delivery_variables = {
                'type': 'DELIVERY',
                'doc_origin': doc_origin,
                'firma': firma,
                'address_delivery': address_delivery,
                'date': date_str,
                'eta_date': date_str,
                'delivery_date': None,
                'cliente': cliente_info,
                'responsable': responsable_info,
                'memo': order.note or '',
                'product_line': product_line,
                'warehouse_id': warehouse_id
            }
            
            _logger.info(f"DEBUG - Doc Origin: {doc_origin}")
            _logger.info(f"DEBUG - Firma: {firma}")
            _logger.info(f"DEBUG - Fecha: {date_str}")
            _logger.info(f"DEBUG - Dirección: {address_delivery[:100]}...")
            
            # 2. CREAR LA PETICIÓN POR LOTES (BATCH)
            # En una sola petición hacemos login y creamos la entrega
            batch_query = '''
                mutation BatchOperations(
                    $loginEmail: String!, 
                    $loginPassword: String!,
                    $type: String, 
                    $doc_origin: String!, 
                    $firma: String, 
                    $address_delivery: String!, 
                    $date: DateTime!, 
                    $eta_date: DateTime, 
                    $delivery_date: DateTime, 
                    $memo: String, 
                    $cliente: ContactInput, 
                    $responsable: ContactInput, 
                    $product_line: [ProductLineInput!]
                    $warehouse_id: Int
                ) {
                    # Operación 1: Login
                    login: login(email: $loginEmail, password: $loginPassword)
                    
                    # Operación 2: Crear entrega (depende del login)
                    delivery: createDeliveryFromOdoo(
                        type: $type
                        doc_origin: $doc_origin
                        firma: $firma
                        address_delivery: $address_delivery
                        date: $date
                        eta_date: $eta_date
                        delivery_date: $delivery_date
                        cliente: $cliente
                        responsable: $responsable
                        memo: $memo
                        product_line: $product_line
                        warehouse_id: $warehouse_id
                    ) {
                        id
                        doc_origin
                        status
                        date
                    }
                }
            '''
            
            # Variables combinadas para el batch
            batch_variables = {
                'loginEmail': laravel_email,
                'loginPassword': laravel_password,
                **delivery_variables
            }
            
            batch_payload = {
                'query': batch_query,
                'variables': batch_variables
            }
            
            _logger.info(f"DEBUG - Enviando batch request...")
            
            # 3. ENVIAR LA PETICIÓN POR LOTES
            response = session.post(
                laravel_url,
                json=batch_payload,
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                },
                timeout=30,
                verify=False
            )
            
            response_text = response.text
            _logger.info(f"DEBUG - Response Status: {response.status_code}")
            _logger.info(f"DEBUG - Response: {response_text[:1000]}")
            
            if response.status_code != 200:
                return self._notify('Error', f'Error HTTP: {response.status_code} - {response_text[:200]}')
            
            try:
                result = response.json()
            except json.JSONDecodeError:
                return self._notify('Error', f'Respuesta no es JSON válido: {response_text[:200]}')
            
            # 4. MANEJAR ERRORES
            if 'errors' in result:
                errors = result.get('errors', [])
                error_messages = []
                
                for error in errors:
                    # Verificar si hay path para saber en qué operación falló
                    path = error.get('path', [])
                    message = error.get('message', str(error))
                    
                    if 'login' in str(path):
                        error_messages.append(f'Login: {message}')
                    elif 'delivery' in str(path) or 'createDeliveryFromOdoo' in str(path):
                        error_messages.append(f'Entrega: {message}')
                    else:
                        error_messages.append(message)
                    
                    # Verificar detalles de validación
                    if 'validation' in error:
                        validation_errors = error.get('validation', {})
                        for field, field_errors in validation_errors.items():
                            error_messages.append(f"Validación {field}: {', '.join(field_errors)}")
                
                error_msg = ' | '.join(error_messages)
                return self._notify('Error', f'Error en operación batch: {error_msg}')
            
            # 5. PROCESAR RESULTADOS
            data = result.get('data', {})
            
            # Verificar login exitoso
            login_result = data.get('login')
            if not login_result:
                return self._notify('Error', 'No se recibió respuesta del login')
            
            _logger.info(f"DEBUG - Login exitoso")
            
            # Verificar entrega creada
            delivery_result = data.get('delivery', {})
            if not delivery_result:
                return self._notify('Error', 'No se creó la entrega')
            
            if not delivery_result.get('id'):
                return self._notify('Error', 'La entrega se envió pero no se recibió ID de confirmación')
            
            _logger.info(f"DEBUG - ✅ Entrega creada exitosamente!")
            _logger.info(f"DEBUG - ID Entrega: {delivery_result.get('id')}")
            _logger.info(f"DEBUG - Status: {delivery_result.get('status')}")
            _logger.info(f"DEBUG - Fecha: {delivery_result.get('date')}")
            
        except requests.exceptions.RequestException as e:
            return self._notify('Error', f'Error de conexión: {str(e)}')
        except Exception as e:
            return self._notify('Error', f'Error en proceso GraphQL: {str(e)}')
           
        finally:
            _logger.info(f"{'='*60}\n")

    def _notify(self, title, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
                'type': 'success' if 'Éxito' in title or 'success' in title.lower() else 'warning'
            }
        }
    
    def map_product_line(self):
        """Mapea productos de Odoo al esquema de Place Vendor"""
        self.ensure_one()
        
        laravel_products = []
        
        for line in self.order_line:
            product = line.product_id
            
            # Mapeo directo de campos
            product_data = {
                # ============ CAMPOS OBLIGATORIOS ============
                'name': product.name or 'Producto sin nombre',
                'description': line.name or product.description_sale or product.description or '',
                
                # ============ CAMPOS OPCIONALES/CALCULADOS ============
                'image': self._get_product_image_url(product),
                'upc': product.barcode or '',  # UPC/EAN normalmente está en barcode
                
                # ============ PRECIOS Y COSTOS ============
                'price': float(line.price_unit),  # Precio de venta unitario
                'cost': float(product.standard_price),  # Coste estándar
                
                # ============ STOCK E INVENTARIO ============
                'stock': int(product.qty_available) if hasattr(product, 'qty_available') else 0,
                'warehouse_stock': self._get_warehouse_stock(product),
                'low_stock': int(product.product_tmpl_id.reordering_min_qty) if hasattr(product.product_tmpl_id, 'reordering_min_qty') else 0,
                
                # ============ SKU Y REFERENCIAS ============
                'sku': product.default_code or '',  # SKU en Odoo
                
                # ============ ESTADO Y FLAGS ============
                'status': self._map_product_status(product),
                'have_variant': bool(product.product_template_attribute_value_ids),
                'permanent': product.active,  # True si está activo en Odoo
                
                # ============ RELACIONES ============
                'company_id': self._get_company_id(),  # ID de compañía en Place Vendor
                'category_id': self._map_category_id(product.categ_id),
                'product_idpadre': self._get_parent_product_id(product),
                
                
                }            
            
            laravel_products.append(product_data)
        
        return laravel_products
    
    # ============ MÉTODOS AUXILIARES ============
    
    def _get_product_image_url(self, product):
        """Obtiene URL de la imagen del producto"""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        
        if hasattr(product, 'image_1920') and product.image_1920:
            return f"{base_url}/web/image/product.product/{product.id}/image_1920"
        elif hasattr(product, 'image_128') and product.image_128:
            return f"{base_url}/web/image/product.product/{product.id}/image_128"
        elif hasattr(product, 'image_64') and product.image_64:
            return f"{base_url}/web/image/product.product/{product.id}/image_64"
        else:
            # Imagen por defecto o placeholder
            return f"{base_url}/web/static/img/placeholder.png"
    
    def _get_warehouse_stock(self, product):
        """Calcula stock por almacén (para warehouse_stock)"""
        if not hasattr(product, 'qty_available'):
            return 0
        
        # Si hay un almacén específico en la orden, usar ese
        warehouse = self.warehouse_id
        if warehouse and hasattr(warehouse, 'lot_stock_id'):
            # Stock en el almacén específico de la orden
            stock_quant = self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', warehouse.lot_stock_id.id),
            ])
            return int(sum(stock_quant.mapped('quantity')) - sum(stock_quant.mapped('reserved_quantity')))
        else:
            # Stock total disponible
            return int(product.qty_available - product.outgoing_qty)
    
    def _map_product_status(self, product):
        """Mapea estado de Odoo a Place Vendor"""
        if not product.active:
            return 'DEACTIVATED'
        elif product.sale_ok and product.active:
            # Si está activo y se puede vender
            if product.product_tmpl_id.website_published:
                return 'PUBLIC'  # Publicado en website
            else:
                return 'PRIVATE'  # Activo pero no publicado
        else:
            return 'DEACTIVATED'
    
    def _get_company_id(self):
        """Obtiene ID de compañía en Place Vendor (necesitas mapear esto)"""
        # Esto depende de cómo tengas mapeadas las compañías
        # Ejemplo: buscar por nombre o tener tabla de mapeo
        company_mapping = {
            'Mi Empresa S.A.': 1,
            'Otra Empresa': 2,
        }
        
        company_name = self.company_id.name
        return company_mapping.get(company_name, 1)  # Default a 1
    
    def _map_category_id(self, odoo_category):
        """Mapea categoría de Odoo a ID en Place Vendor"""
        # Necesitas una tabla de mapeo o lógica específica
        # Ejemplo básico:
        category_mapping = {
            'Todo / Venta': 1,
            'Todo / Venta / Productos': 2,
            'Todo / Venta / Servicios': 3,
            # Agrega más mapeos según necesites
        }
        
        if odoo_category:
            category_path = odoo_category.complete_name
            return category_mapping.get(category_path, 1)  # Default a 1
        
        return 1  # Categoría por defecto
    
    def _get_parent_product_id(self, product):
        """Obtiene ID del producto padre si es variante"""
        if product.product_tmpl_id.product_variant_count > 1:
            # Es una variante, buscar el producto padre en Place Vendor
            # Necesitas mantener un mapeo de productos Odoo->Place Vendor
            parent_mapping = self._get_parent_product_mapping()
            return parent_mapping.get(product.product_tmpl_id.id, None)
        return None
    
    def _prepare_contact_info(self, partner, contact_type='Cliente'):
        """Prepara información de contacto para GraphQL"""
        if not partner:
            return {
                'name': f'{contact_type} no especificado',
                'email': '',
                'phone': '',
                'address': '',
                'city': '',
                'country': '',
                'state': '',
                'postal_code': '',
                'employed_occupation': ''
            }
        
        # Obtener dirección del partner
        address_parts = []
        if partner.street:
            address_parts.append(partner.street)
        if partner.street2:
            address_parts.append(partner.street2)
        address = ', '.join(address_parts) if address_parts else ''
        
        # Obtener país, estado, ciudad
        country_name = partner.country_id.name if partner.country_id else ''
        state_name = partner.state_id.name if partner.state_id else ''
        city = partner.city or ''
        
        return {
            'name': partner.name or '',
            'email': partner.email or '',
            'phone': partner.phone or partner.mobile or '',
            'address': address,
            'city': city,
            'country': country_name,
            'state': state_name,
            'postal_code': partner.zip or '',
            'employed_occupation': partner.function  or ''
        }
    
    def _prepare_product_line(self, order):
        """Prepara la línea de productos para GraphQL"""
        product_line = []
        
        for line in order.order_line:
            product = line.product_id
            
            # Prepara la categoría
            category_input = {
                'name': product.categ_id.name or 'Sin categoría',
                'description': product.categ_id.complete_name or ''
            } if product.categ_id else {
                'name': 'Sin categoría',
                'description': ''
            }
            
            # Prepara el producto
            product_input = {
                'name': product.name or 'Producto sin nombre',
                'description': line.name or product.description_sale or product.description or '',
                'image': self._get_product_image_url(product),
                'price': float(line.price_unit),
                'cost': float(product.standard_price),
                'stock': int(product.qty_available) if hasattr(product, 'qty_available') else 0,
                'warehouse_stock': self._get_warehouse_stock(product),
                'low_stock': int(product.product_tmpl_id.reordering_min_qty) if hasattr(product.product_tmpl_id, 'reordering_min_qty') else 10,
                'sku': product.default_code or '',
                'upc': product.barcode or '',
                'status': self._map_product_status(product),
                'have_variant': bool(product.product_template_attribute_value_ids),
                'category': category_input,
            }
            
            # Prepara la línea del producto
            product_line_input = {
                'cant': int(line.product_uom_qty),
                'product': product_input,
                'model_id': line.id,  # ID de la línea en Odoo
                'model_type': 'sale_order_line',
                'description': line.name or product.name,
            }
            
            product_line.append(product_line_input)
        
        return product_line

    def _autenticacion_placevendor(self):
        # Obtener configuración del usuario actual
        config = self.env['placevendor.config'].search([
            ('odoo_user_id', '=', self.env.user.id),
            ('company_id', '=', self.env.company.id),
            ('active', '=', True)
        ], limit=1)
        
        if not config:
            #"No hay configuración de Place Vendor para este usuario"
            return 1
        
        if not config.is_authenticated:
           # "No estás autenticado en Place Vendor"
            return 2
        
        return config
    
    def get_warehouses_by_company(self, warehouse_name=None):
        """Obtiene almacenes por compañía desde la API GraphQL"""
        auth_config =  self._autenticacion_placevendor()
       

        if auth_config==1:
            return self._notify('Error', "No hay configuración de Place Vendor para este usuario")
        if auth_config==2:
            return self._notify('Error', "No estás autenticado en Place Vendor")
        
        laravel_url = auth_config['laravel_url']
        laravel_email = auth_config['laravel_user']
        laravel_password = auth_config['laravel_password']
        
        # Construir la mutación GraphQL
        query = """
        mutation GetWarehousesByCompany(
                    $loginEmail: String!, 
                    $loginPassword: String!,
                    $name: String, 
                    $first: Int, 
                    $page: Int) {
            # Operación 1: Login
            login: login(email: $loginEmail, password: $loginPassword)

            warehouses :getWarehousesByCompany(
                name: $name
                first: $first
                page: $page
            ) {
                data {
                    id
                    name
                    address
                    description
                    company_id
                }
                paginatorInfo {
                    total
                    perPage
                    currentPage
                    lastPage
                    hasMorePages
                }
            }
        }
        """
        
        variables = {
             # Variables combinadas para el batch
           
            'loginEmail': laravel_email,
            'loginPassword': laravel_password,
           
            "first": 50,  # Puedes ajustar este valor
        }
        
        if warehouse_name:
            variables["name"] = warehouse_name
            
        payload = {
            "query": query,
            "variables": variables
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        
        try:
            response = requests.post(
                laravel_url,
                data=json.dumps(payload),
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()
            result = response.json()
            
            if 'errors' in result:
                _logger.error(f"Error GraphQL: {result['errors']}")
                return []
                
            warehouses_data = result.get('data', {}).get('warehouses', {}).get('data', [])
            
            # Procesar los almacenes obtenidos
            """ warehouses = []
            for wh in warehouses_data:
                #_logger.error(f"WarehousesL: {wh}")  
                warehouses.append({
                    'external_id': wh['id'],
                    'name': wh['name'],
                    'address': wh.get('address', ''),
                    'description': wh.get('description', ''),
                    'company_id': wh['company_id']
                }) """
            
            return warehouses_data
            
        except requests.exceptions.RequestException as e:
            _logger.error(f"Error en la conexión: {str(e)}")
            return []
        except json.JSONDecodeError as e:
            _logger.error(f"Error decodificando JSON: {str(e)}")
            return []
    
    def action_open_warehouse_window(self):
        """Abre el wizard existente para seleccionar almacén"""
        self.ensure_one()
        
        # Verificar que exista al menos un almacén disponible
        warehouses = self.get_warehouses_by_company()
        _logger.info(f"Alamacenes : {warehouses}")
        if hasattr(warehouses,'type'):
             _logger.info(f"hasattr(warehouses,'tag') : ")
             return warehouses
        
        # Si solo hay un almacén, usarlo directamente
        if len(warehouses) == 1:
            return self.send_delivery_to_laravel(warehouses[0].id)
        
        # Crear lista de opciones para selección rápida
        warehouse_list = []
        for wh in warehouses:
            warehouse_list.append((
                wh['id'],
                f"{wh['name']} - {wh['address'] or 'Sin dirección'}"
            ))
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Seleccionar Almacén',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'view_id': self.env.ref('odoo_placevendor_delivery.view_warehouse_selection_form').id,
            'res_id': self.id,
            'target': 'new',
            'context': {
                'warehouse_list': warehouse_list,
                'default_warehouse_selection': True,
            }
        }

    
    def _get_warehouse_selection(self):
        """Método dinámico para obtener la lista de almacenes"""
        warehouses = self.get_warehouses_by_company()
        _logger.error(f"Error en la conexión: {warehouses}")
        selection = []
        if  hasattr(warehouses, 'id'):
            for wh in warehouses:
                selection.append((
                    wh['id'],
                    f"{wh['name']} - {wh['address'] or 'Sin dirección'}"
                ))
        return selection
    
    def _compute_warehouse_count(self):
        for order in self:
            warehouses = order.get_warehouses_by_company()
            order.warehouse_count = len(warehouses)
    
    def action_confirm_warehouse_selection(self):
        """Confirmar la selección y enviar"""
        self.ensure_one()
        
        if not self.warehouse_selection:
            return self._notify('Error', "Debe seleccionar un almacén")
            
        
        warehouse_id = int(self.warehouse_selection)
        return self.send_delivery_to_laravel(warehouse_id)

    def _get_parent_product_mapping(self):
        """Devuelve mapeo de productos padre (debes mantener esto actualizado)"""
        # Esto debería venir de una tabla de mapeo en Odoo o Place Vendor
        return {
            # template_id_odoo: laravel_parent_id
            123: 456,  # Ejemplo
        }