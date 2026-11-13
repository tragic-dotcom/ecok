# EcoBlack V17 — reparación del mapa y temas del panel administrador

EcoBlack conserva su estructura Flask. En Render, `app.py` selecciona MongoDB Atlas mediante `MONGODB_URI`; en local, si esa variable no existe, usa SQLite.

## Acceso por plan

- Cada cuenta nueva comienza **Sin plan**. La tarjeta de inicio muestra el plan actual y el estado de acceso.
- Una cuenta normal debe comprar un plan antes de iniciar una carga. Puede consultar el mapa y los servicios, pero el servidor rechaza conexiones sin un plan activo.
- Los planes **Básico, Rápido, Power y Personalizado** incluyen una sesión. Se pagan antes de conectar; al iniciar esa sesión, el plan se consume y vuelve a quedar la cuenta en **Sin plan**.
- No se vuelve a descontar saldo al conectar una sesión que ya fue comprada.
- Una cuenta no puede comprar otro plan estándar mientras conserve uno listo para usar.
- El servidor verifica el plan vigente y el tipo de módulo en cada conexión. No basta con cambiar el selector del navegador.

## Bmax Pro: acceso VIP exclusivo

- Bmax Pro solo se puede comprar desde su apartado limitado y solo puede utilizarse en módulos que tengan `module_type: "bmax"`.
- Un plan normal no puede abrir un módulo Bmax. Bmax Pro tampoco puede abrir un módulo normal.
- Hay 10 cupos iniciales. El precio inicial es **$99 MXN**, editable desde el panel de administrador.
- Antes de confirmar, el usuario aparta un lugar durante **5 minutos**. El servidor libera los apartados vencidos y controla el inventario en Atlas para evitar sobreventas.
- Al confirmar la compra, Bmax Pro queda permanente en la cuenta y el usuario puede realizar sesiones Bmax posteriores sin volver a comprar el plan.
- Si no hay cupos, el servicio aparece como **Agotado**. El administrador puede aumentar o reducir el total, pero no por debajo de los cupos vendidos o apartados.
- Bmax Pro tiene un límite máximo de paquete de **100 W**. La potencia efectiva es el menor valor entre el límite Bmax, la capacidad estimada del teléfono, la potencia registrada del módulo y el límite del conector.
- La sesión estima el tiempo hasta 100% usando el porcentaje inicial y la capacidad del dispositivo. Cuando el navegador admite `navigator.getBattery()`, intenta leer la batería; si no, el usuario puede ajustarla manualmente.

## Módulos y mapa

- Desde **Administración → Añadir módulo**, el admin puede crear módulos EcoBlack normales o módulos `Bmax · negro / morado`.
- Cada módulo nuevo requiere nombre, ciudad, estado y coordenadas. Usa **Elegir punto en el mapa** para fijar la posición; si pulsas **Añadir al mapa** sin coordenadas, la aplicación abre automáticamente el modo de selección para que no parezca que el botón no funciona. Escribe su ciudad/estado reales para que se agrupe correctamente.
- Los módulos Bmax tienen carcasa negra y detalles violetas; cuando están ocupados muestran su halo especial.
- Los módulos permanecen anclados a las coordenadas guardadas. El marcador no se desplaza por animaciones de escala cuando se acerca o aleja el mapa.
- El mapa cambia de nivel de detalle: **vista regional/estatal** con nombre del estado y número de módulos; **vista de ciudad** con nombre de ciudad y conteo; y, al acercarse, **marcadores individuales** en su ubicación guardada.
- El admin puede usar el botón **Ciudad/estado** para completar metadatos de módulos antiguos que todavía no tengan esos campos y **Reubicar** para corregir su posición exacta.
- Para los módulos predeterminados de Cancún, la aplicación completa automáticamente `Cancún, Quintana Roo` en registros antiguos que no tengan esos valores. No reemplaza nombres geográficos que ya estén configurados.

## Apariencia por plan y animación Bmax

- Cada plan cambia el color de acento de la interfaz y de los marcadores normales.
- Bmax Pro aplica una apariencia VIP morada a toda la interfaz.
- Al conectar un módulo Bmax aparece una animación exclusiva con núcleo luminoso, aura espectral violeta, niebla, destellos y anillos de energía. Respeta la preferencia de movimiento reducido del dispositivo.

## Persistencia en MongoDB Atlas

La aplicación crea las colecciones e índices que necesita al iniciar. Bmax usa:

- `bmax_config`: cupos totales, vendidos, apartados y precio.
- `bmax_reservations`: apartados temporales y su vencimiento.
- `bmax_purchases`: compras y acceso permanente del usuario.

Los planes estándar guardan el plan activo y su estado en el documento del usuario (`active_plan`, `active_plan_status`, `active_plan_minutes` y campos de compra/consumo). Los módulos guardan `module_type`, `city` y `state`; el perfil admin puede guardar `admin_service_theme`. No hace falta crear estas colecciones o campos manualmente.

## Variables de entorno en Render

Conserva las variables actuales del servicio:

- `MONGODB_URI`: cadena de conexión de MongoDB Atlas.
- `MONGODB_DB`: normalmente `ecoblack`.
- `SECRET_KEY`: clave larga y aleatoria.
- `ADMIN_USERNAME`: usuario administrador ya configurado.
- `ADMIN_PASSWORD`: contraseña segura del administrador.

No subas `.env`, contraseñas ni cadenas de MongoDB a GitHub. Esta versión no requiere cambiar `MONGODB_URI` ni `MONGODB_DB`.

## Pruebas recomendadas tras desplegar

1. Crea una cuenta normal y confirma que Inicio muestre **Sin plan**.
2. Intenta conectar cualquier módulo sin comprar: el servidor debe rechazarlo.
3. Compra un plan estándar, conecta un módulo normal y confirma que la sesión se incluye sin un segundo cargo; al comenzar, la cuenta debe volver a **Sin plan**.
4. Intenta conectar un módulo Bmax con un plan estándar: debe rechazarlo.
5. Compra Bmax Pro con una cuenta distinta, usando el apartado de 5 minutos, y confirma que se vea el acceso VIP morado.
6. Con Bmax Pro, conecta un módulo Bmax: debe funcionar. Intenta conectar un módulo normal: debe rechazarlo.
7. Prueba el mapa a escala estatal, de ciudad y de calle. Revisa nombres y conteos; abre un pin y confirma que la ubicación coincide con las coordenadas guardadas.
8. Desde el admin, completa ciudad/estado de módulos antiguos, crea un módulo nuevo y confirma que persista en Atlas.
9. Prueba dos cuentas intentando apartar el último cupo Bmax; solo una debe obtenerlo.

## Nota técnica sobre carga real

Esta versión implementa planes, reservas, compras, autorizaciones, mapa y estimaciones dentro de la aplicación web. **No controla eléctricamente un cargador ni puede garantizar una carga física de 100 W o una batería al 100%** sin integrar el hardware, los protocolos de carga y telemetría real. La potencia y los minutos mostrados son estimaciones de software.


## V17 · Reparación de mapa y panel admin

- El formulario ya no acepta coordenadas vacías ni el punto accidental `0, 0`; exige una ubicación real antes de guardar.
- El admin puede reubicar módulos existentes desde el mapa, útil para corregir módulos guardados anteriormente en una esquina o con coordenadas incompletas.
- Los módulos sin coordenadas válidas ya no generan pines fantasma ni agrupaciones falsas; aparecen marcados como `Ubicación pendiente` en el panel admin.
- Al guardar un módulo, la app cambia primero a la vista del mapa, espera que el mapa esté visible/listo, lo redimensiona y centra el nuevo pin en sus coordenadas reales.
- El panel de administrador inicia con tema vino. Se agregó un selector de vista para los temas Básico (verde), Rápido (azul), Power (naranja), Personalizado (turquesa) y Bmax Pro (violeta). La preferencia se guarda en el perfil del admin en Atlas/SQLite. El selector solo cambia el aspecto del panel admin, no cambia planes ni permisos de clientes.
- Las validaciones se aplican tanto al backend MongoDB Atlas como al backend local SQLite.
- Se corrigió el posicionamiento de MapLibre: el pin ahora usa un contenedor geográfico independiente del botón visual, de modo que las reglas CSS de animación no anulan la transformación de coordenadas del mapa.
