from odoo import models, fields, api
import requests
import json
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging

_logger = logging.getLogger(__name__)

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # Campo temporal para almacenar la selección
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

    def send_reception_to_laravel(self, warehouse_id):
        """Envía la recepción a Place Vendor vía GraphQL"""
        for order in self:
            _logger.info(f"orden {order}")
            if not hasattr(order, 'picking_ids') or not order.picking_ids:
                _logger.error(f"No hay recepciones para esta orden")
                return self._notify('Error', 'No hay recepciones para esta orden')
            
            errors = []
            for picking in order.picking_ids:
                    _logger.error(f"entrando al for {picking.name}")    
                # Filtrar solo recepciones (entradas)
                #if picking.picking_type_id.code == 'incoming':
                    try:
                        self._send_graphql_mutation(picking, order, warehouse_id)
                    except Exception as e:
                        _logger.error(f"hubo un errorcito ")    
                        errors.append(f"{picking.name}: {str(e)}")

            if errors:
                return self._notify('Error enviando a Place Vendor', '\n'.join(errors))

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Éxito',
                    'message': 'Recepción(es) enviada(s) a Place Vendor',
                    'sticky': False,
                    'type': 'success',
                    'next': {'type': 'ir.actions.act_window_close'}
                }
            }

    def _send_graphql_mutation(self, picking, order, warehouse_id):
        """Envía la mutación GraphQL para crear una recepción"""
        auth_config = self._autenticacion_placevendor()
        _logger.error(f"Se autenticó")
        
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
            _logger.info(f"DEBUG - Enviando recepcion: {picking.name}")
            _logger.info(f"DEBUG - URL: {laravel_url}")
            _logger.info(f"{'='*60}")

            # OBTENER LOS PRODUCTOS DE LA ORDEN
            product_line = self._prepare_product_line(order)
            _logger.info(f"DEBUG - Productos a enviar: {len(product_line)}")
            
            # PREPARAR LAS VARIABLES PARA LA RECEPCIÓN
            scheduled_date = picking.scheduled_date or datetime.now()
            
            # Formato ISO 8601
            date_str = scheduled_date.strftime('%Y-%m-%d %H:%M:%S')
            
            # Obtener fecha de recepción (si ya se recibió)
            receive_date = None
            if picking.state == 'done' and picking.date_done:
                receive_date = picking.date_done.strftime('%Y-%m-%d %H:%M:%S')
            
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
            
            # Información del proveedor
            proveedor_info = self._prepare_contact_info(order.partner_id, 'Proveedor')
            
            # Información del responsable
            responsable_info = self._prepare_contact_info(order.user_id, 'Responsable')
            
            doc_origin = picking.name or f"IN-{picking.id}"
            
            # Variables para la mutación de recepción
            reception_variables = {
                'doc_origin': doc_origin,
                'receive_date': receive_date,
                'date': date_str,
                'eta_date': date_str,
                'delivery_date': None,
                'memo': order.notes or order.origin or '',
                'responsable': responsable_info,
                'product_line': product_line,
                'warehouse_id': warehouse_id
            }
            
            _logger.info(f"DEBUG - Doc Origin: {doc_origin}")
            _logger.info(f"DEBUG - Fecha: {date_str}")
            _logger.info(f"DEBUG - Dirección: {address_delivery[:100]}...")
            
            # CREAR LA PETICIÓN POR LOTES (BATCH)
            batch_query = '''
                mutation BatchOperations(
                    $loginEmail: String!, 
                    $loginPassword: String!,
                    $doc_origin: String!, 
                    $receive_date: DateTime, 
                    $date: DateTime!, 
                    $eta_date: DateTime, 
                    $delivery_date: DateTime, 
                    $memo: String, 
                    $responsable: ContactInput, 
                    $product_line: [ProductLineInput!],
                    $warehouse_id: Int
                ) {
                    # Operación 1: Login
                    login: login(email: $loginEmail, password: $loginPassword)
                    
                    # Operación 2: Crear recepción
                    reception: createReceptionFromOdoo(
                        doc_origin: $doc_origin
                        receive_date: $receive_date
                        date: $date
                        eta_date: $eta_date
                        delivery_date: $delivery_date
                        memo: $memo
                        responsable: $responsable
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
                **reception_variables
            }
            
            batch_payload = {
                'query': batch_query,
                'variables': batch_variables
            }
            
            _logger.info(f"DEBUG - Enviando batch request...")
            
            # ENVIAR LA PETICIÓN POR LOTES
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
            
            # MANEJAR ERRORES
            if 'errors' in result:
                errors = result.get('errors', [])
                error_messages = []
                
                for error in errors:
                    path = error.get('path', [])
                    message = error.get('message', str(error))
                    
                    if 'login' in str(path):
                        error_messages.append(f'Login: {message}')
                    elif 'reception' in str(path) or 'createReceptionFromOdoo' in str(path):
                        error_messages.append(f'Recepción: {message}')
                    else:
                        error_messages.append(message)
                    
                    if 'validation' in error:
                        validation_errors = error.get('validation', {})
                        for field, field_errors in validation_errors.items():
                            error_messages.append(f"Validación {field}: {', '.join(field_errors)}")
                
                error_msg = ' | '.join(error_messages)
                return self._notify('Error', f'Error en operación batch: {error_msg}')
            
            # PROCESAR RESULTADOS
            data = result.get('data', {})
            
            # Verificar login exitoso
            login_result = data.get('login')
            if not login_result:
                return self._notify('Error', 'No se recibió respuesta del login')
            
            _logger.info(f"DEBUG - Login exitoso")
            
            # Verificar recepción creada
            reception_result = data.get('reception', {})
            if not reception_result:
                return self._notify('Error', 'No se creó la recepción')
            
            if not reception_result.get('id'):
                return self._notify('Error', 'La recepción se envió pero no se recibió ID de confirmación')
            
            _logger.info(f"DEBUG - ✅ Recepción creada exitosamente!")
            _logger.info(f"DEBUG - ID Recepción: {reception_result.get('id')}")
            _logger.info(f"DEBUG - Status: {reception_result.get('status')}")
            _logger.info(f"DEBUG - Fecha: {reception_result.get('date')}")
            
        except requests.exceptions.RequestException as e:
            return self._notify('Error', f'Error de conexión: {str(e)}')
        except Exception as e:
            return self._notify('Error', f'Error en proceso GraphQL: {str(e)}')
        finally:
            _logger.info(f"{'='*60}\n")

    def _prepare_product_line(self, order):
        """Prepara la línea de productos para GraphQL (para compras)"""
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
                'description': line.name or product.description_purchase or product.description or '',
                'image': self._get_product_image_url(product),
                'price': float(line.price_unit),  # Precio de compra
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
                'cant': int(line.product_qty),
                'product': product_input,
                'model_id': line.id,  # ID de la línea en Odoo
                'model_type': 'purchase_order_line',
                'description': line.name or product.name,
            }
            
            product_line.append(product_line_input)
        
        return product_line

    def _map_picking_status(self, odoo_status):
        """Mapea el estado del picking de Odoo a Place Vendor"""
        status_mapping = {
            'draft': 'PENDING',
            'waiting': 'PENDING',
            'confirmed': 'CONFIRMED',
            'assigned': 'ASSIGNED',
            'partially_available': 'PARTIAL',
            'done': 'COMPLETED',
            'cancel': 'CANCELLED'
        }
        return status_mapping.get(odo_state, 'PENDING')

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

    def _autenticacion_placevendor(self):
        # Obtener configuración del usuario actual
        config = self.env['placevendor.config'].search([
            ('odoo_user_id', '=', self.env.user.id),
            ('company_id', '=', self.env.company.id),
            ('active', '=', True)
        ], limit=1)
        
        if not config:
            return 1  # "No hay configuración de Place Vendor para este usuario"
        
        if not config.is_authenticated:
            return 2  # "No estás autenticado en Place Vendor"
        
        return config

    def _prepare_contact_info(self, partner, contact_type='Responsable'):
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
            'employed_occupation': partner.function or ''
        }

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
            return f"{base_url}/web/static/img/placeholder.png"

    def _get_warehouse_stock(self, product):
        """Calcula stock por almacén"""
        if not hasattr(product, 'qty_available'):
            return 0
        
        return int(product.qty_available - product.outgoing_qty)

    def _map_product_status(self, product):
        """Mapea estado de Odoo a Place Vendor"""
        if not product.active:
            return 'DEACTIVATED'
        elif product.purchase_ok and product.active:
            if product.product_tmpl_id.website_published:
                return 'PUBLIC'
            else:
                return 'PRIVATE'
        else:
            return 'DEACTIVATED'

    def get_warehouses_by_company(self, warehouse_name=None):
        """Obtiene almacenes por compañía desde la API GraphQL"""
        auth_config = self._autenticacion_placevendor()
        if auth_config == 1:
            return self._notify('Error', "No hay configuración de Place Vendor para este usuario")
        if auth_config == 2:
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
            'loginEmail': laravel_email,
            'loginPassword': laravel_password,
            "first": 50,
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
            
            return warehouses_data
            
        except requests.exceptions.RequestException as e:
            _logger.error(f"Error en la conexión: {str(e)}")
            return []
        except json.JSONDecodeError as e:
            _logger.error(f"Error decodificando JSON: {str(e)}")
            return []

    def action_open_warehouse_window(self):
        """Abre la ventana para seleccionar almacén"""
        self.ensure_one()
        
        # Verificar que exista al menos un almacén disponible
        warehouses = self.get_warehouses_by_company()
        
        if isinstance(warehouses, dict) or 'type' in warehouses:
            return warehouses
        
        # Si solo hay un almacén, usarlo directamente
        if len(warehouses) == 1:
            return self.send_reception_to_laravel(warehouses[0]['id'])
        
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
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'view_id': self.env.ref('integracion_placevendor_odoo.view_warehouse_selection_form_purchase').id,
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
        _logger.info(f"warehouses obtenidos: {warehouses}")
        selection = []
        
        for wh in warehouses:
            selection.append((
                str(wh['id']),
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
        return self.send_reception_to_laravel(warehouse_id)