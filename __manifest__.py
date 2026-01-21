{
    'name': 'Place Vendor Delivery Integration',
    'version': '1.0',
    'category': 'Sales',
    'summary': 'Integración para enviar entregas a Place Vendor vía GraphQL',
    
    'description': """
    Módulo de integración entre Odoo y Place Vendor para el envío de entregas.

    INSTRUCCIONES DE USO:

    1. CONFIGURACIÓN INICIAL:
       Ir a: Place Vendor → Configuración
       Ingresar:
       * Endpoint GraphQL: URL de la API de Place Vendor
       * Email de Acceso: Email del usuario para Place Vendor
       * Password de Acceso: Password del usuario para Place Vendor

    2. SINCRONIZAR ALMACENES:
       Ir a: Ventas → Pedidos de Venta
       Seleccionar un pedido en estado "Confirmado"
       Hacer clic en "Enviar Entrega a Place Vendor"
       Si es primera vez, el sistema sincronizará automáticamente los almacenes disponibles

    3. ENVIAR ENTREGA:
       En el pedido confirmado, hacer clic en "Enviar Entrega a Place Vendor"
       Se abrirá una ventana con los almacenes disponibles:
       * Si hay 1 solo almacén: Se enviará automáticamente
       * Si hay varios almacenes: Seleccionar uno y hacer clic en "Confirmar y Enviar"
       El sistema enviará la información a Place Vendor y cerrará la ventana automáticamente

    4. VERIFICAR ENVÍOS:
       Los envíos exitosos se registran en el chatter del pedido
       En caso de error, se mostrará un mensaje con los detalles
       Revisar los logs de Odoo para diagnóstico avanzado

    FUNCIONALIDADES PRINCIPALES:
    - Autenticación segura con Place Vendor
    - Sincronización automática de almacenes
    - Selección interactiva de almacén
    - Integración directa con órdenes de venta
    - Manejo de errores y notificaciones
    - Registro de auditoría en chatter

    REQUISITOS TÉCNICOS:
    - Odoo 18.0 o superior
    - Conexión a internet
    - Credenciales válidas de Place Vendor
    - Permisos de ventas para los usuarios
    """,
    
    'author': 'Roberto León Socarrás',
    'depends': ['base', 'sale', 'sale_stock', 'mail'],
    
    'data': [
        'views/sale_order_view.xml',
        'views/placevendor_config_views.xml',
        'security/ir.model.access.csv'
    ],
    
    
    'images': [
        'static/description/icon.png',
        'static/description/config.jpg',
        'static/description/warehouses.jpg',
        'static/description/sale.jpg'
    ],

    
    'icon': '/odoo_placevendor_delivery/static/description/icon.png',

    'support': 'robertoleonsocarras@gmail.com',
    'license': 'LGPL-3',
    'installable': True,
    'application': True,
    'auto_install': False,
    
    'external_dependencies': {
        'python': ['requests'],
    },
}