# models/placevendor_config.py
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import timedelta
import requests
import logging

_logger = logging.getLogger(__name__)

class PlaceVendorConfig(models.Model):
    _name = 'placevendor.config'
    _description = 'Configuración de Autenticación Place Vendor'
    _rec_name = 'laravel_user'
    _order = 'create_date desc'
    
    # Campos básicos (mantén solo los esenciales por ahora)
    laravel_user = fields.Char(
        string='Usuario Place Vendor',
        required=True
    )
    
    laravel_password = fields.Char(
        string='Contraseña Place Vendor',
        required=True
    )
    
    laravel_url = fields.Char(
        string='URL GraphQL',
        required=True,
        default='http://placevendor.com/graphql'
    )
    
    # Relaciones
    odoo_user_id = fields.Many2one(
        'res.users',
        string='Usuario Odoo',
        default=lambda self: self.env.user.id,
        readonly=True
    )
    
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company.id,
        required=True
    )
    
    # Estado
    is_authenticated = fields.Boolean(
        string='Autenticado',
        default=False,
        readonly=True
    )
    
    last_authentication = fields.Datetime(
        string='Última autenticación',
        readonly=True
    )
    
    authentication_error = fields.Text(
        string='Error de autenticación',
        readonly=True
    )
    
    token = fields.Char(string='Token', readonly=True)
    token_expiration = fields.Datetime(string='Token expira', readonly=True)
    
    # Control
    active = fields.Boolean(string='Activo', default=True)
    
    # Restricciones
    _sql_constraints = [
        ('unique_user_company', 
         'UNIQUE(odoo_user_id, company_id)', 
         'Ya existe una configuración para este usuario y compañía'),
    ]
    
    def test_authentication(self):
        """Probar autenticación con Place Vendor"""
        for record in self:
            try:
                # GraphQL mutation para login
                graphql_mutation = """
                    mutation Login($email: String!, $password: String!) {
                        login(email: $email, password: $password)
                    }
                """
                
                payload = {
                    'query': graphql_mutation,
                    'variables': {
                        'email': record.laravel_user,
                        'password': record.laravel_password
                    }
                }
                
                response = requests.post(
                    record.laravel_url,
                    json=payload,
                    headers={'Content-Type': 'application/json'},
                    timeout=10,
                    verify=False
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'errors' in data:
                        error_msg = data['errors'][0]['message'] if data['errors'] else 'Error desconocido'
                        record.write({
                            'is_authenticated': False,
                            'authentication_error': error_msg,
                        })
                        return self._show_notification('Error', error_msg, 'danger')
                    
                    if 'data' in data and data['data']['login']:
                        token = data['data']['login']
                        
                        record.write({
                            'is_authenticated': True,
                            'last_authentication': fields.Datetime.now(),
                            'authentication_error': False,
                            'token': token,
                            'token_expiration': fields.Datetime.now() + timedelta(hours=24)
                        })
                        
                        return self._show_notification('Éxito', 'Autenticación exitosa', 'success')
                        
                else:
                    raise Exception(f'Error HTTP {response.status_code}')
                    
            except Exception as e:
                error_msg = str(e)
                record.write({
                    'is_authenticated': False,
                    'authentication_error': error_msg
                })
                return self._show_notification('Error', error_msg, 'danger')
    
    def _show_notification(self, title, message, type):
        """Mostrar notificación"""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': type,
                'sticky': False,
            }
        }
    
    @api.model
    def get_config(self):
        """Obtener configuración activa para el usuario actual"""
        return self.search([
            ('odoo_user_id', '=', self.env.user.id),
            ('company_id', '=', self.env.company.id),
            ('active', '=', True),
            ('is_authenticated', '=', True)
        ], limit=1)