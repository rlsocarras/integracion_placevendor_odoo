Place Vendor Delivery Integration
=================================

Integración entre Odoo y Place Vendor para el envío de entregas mediante GraphQL.

Instrucciones de Uso
--------------------

1. Configuración Inicial
   ~~~~~~~~~~~~~~~~~~~~~~
   - Ir a: Place Vendor → Configuración
   - Ingresar:
     * Endpoint GraphQL: URL de la API de Place Vendor
     * Email de Acceso: Email del usuario
     * Password de Acceso: Password del usuario

2. Sincronizar Almacenes
   ~~~~~~~~~~~~~~~~~~~~~~
   - Ir a: Ventas → Pedidos de Venta
   - Seleccionar pedido en estado "Confirmado"
   - Hacer clic en "Enviar Entrega a Place Vendor"

3. Enviar Entrega
   ~~~~~~~~~~~~~~~
   - En pedido confirmado, clic en "Enviar Entrega a Place Vendor"
   - Seleccionar almacén y confirmar

Características Principales
---------------------------
- Autenticación segura con Place Vendor
- Sincronización automática de almacenes
- Selección interactiva de almacén
- Integración directa con órdenes de venta

Requisitos Técnicos
-------------------
- Odoo 18.0 o superior
- Conexión a internet
- Credenciales válidas de Place Vendor