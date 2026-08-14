# Guía: crear la WABA productiva directamente en Meta (sin WATI)

Para Jaime — paso a paso en tu Business Manager con la línea telefónica que
ya tienes. Al final, el webhook queda apuntando al sistema y Claude verifica
la firma y la entrega desde el backend.

> **⚠️ Antes de empezar — si la línea está registrada en WATI:** un número
> solo puede vivir en un BSP a la vez. Pide en WATI la *liberación del
> número* (off-boarding / delete phone number de su WABA). Hasta que WATI lo
> suelte, Meta no te dejará registrarlo. Si la línea nunca pasó por WATI,
> ignora esto.

## 1. Crear la app en Meta for Developers

1. Entra a https://developers.facebook.com → **My Apps** → **Create App**.
2. Tipo: **Business** → vincúlala a tu **Business Portfolio** (el Business
   Manager de NODO/Colombia Unida).
3. Nombre sugerido: `Colombia Unida WhatsApp`.
4. En el dashboard de la app → **Add product** → **WhatsApp** → Set up.
   Esto crea (o te deja elegir) la **WABA** dentro de tu portfolio.

## 2. Registrar tu número

1. En WhatsApp → **API Setup** → sección *From* → **Add phone number**.
2. Ingresa el nombre visible (ej. "Colombia Unida"), categoría y el número.
3. Verifícalo por SMS o llamada. *(Si falla aquí, casi siempre es porque el
   número sigue amarrado a WATI u otra WABA — ver advertencia arriba.)*
4. Anota el **Phone number ID** y el **WABA ID** que muestra esa pantalla.

## 3. Token permanente (system user)

El token temporal del dashboard expira en 24h; no lo uses para producción.

1. Business Manager → **Configuración del negocio** → **Usuarios** →
   **Usuarios del sistema** → **Agregar** → tipo **Admin** (o Employee),
   nombre `colombia-unida-backend`.
2. **Asignar activos** → la app `Colombia Unida WhatsApp` → permiso
   *Administrar app*; y la WABA → permiso *Administrar*.
3. **Generar token** → selecciona la app → permisos mínimos:
   `whatsapp_business_messaging` y `whatsapp_business_management` →
   sin expiración.
4. Copia el token **una sola vez** y guárdalo en el vault. Nunca en el chat
   público ni en el repo.

## 4. Configurar el webhook hacia el sistema

1. App → WhatsApp → **Configuration** → **Webhook** → Edit:
   - **Callback URL:** `https://nodo.host/colombia-unida/api/webhooks/meta/whatsapp`
   - **Verify token:** el valor de `META_WEBHOOK_VERIFY_TOKEN` (genera uno
     aleatorio y guárdalo en el vault + EasyPanel).
2. Meta hará el GET de verificación — el backend responde el challenge
   automáticamente si el token coincide.
3. En **Webhook fields** suscribe: `messages` (incluye estados de entrega).

## 5. Variables de entorno en EasyPanel (servicios api y worker)

```
META_GRAPH_API_VERSION=v23.0
META_WABA_ID=<WABA ID del paso 2>
META_PHONE_NUMBER_ID=<Phone number ID del paso 2>
META_ACCESS_TOKEN=<token del system user, paso 3>
META_APP_SECRET=<App → Settings → Basic → App Secret>
META_WEBHOOK_VERIFY_TOKEN=<el mismo del paso 4>
```

Después de guardarlas: **Implementar** de nuevo api y worker.

## 6. Plantillas de mensaje (para escribir fuera de la ventana de 24h)

WhatsApp → **Message templates** → crear y enviar a aprobación (es):

| Nombre | Uso | Texto sugerido |
|---|---|---|
| `recordatorio_caso` | Retomar draft inactivo | "Hola {{1}}, tu solicitud {{2}} en Colombia Unida quedó incompleta. Respóndenos cuando puedas y la completamos juntos." |
| `avance_caso` | Notificar hito | "Buenas noticias: tu caso {{1}} tiene un avance: {{2}}. Escríbenos si tienes preguntas." |
| `confirmacion_entrega` | Pedir OTP/confirmación | "Colombia Unida: para confirmar la entrega del caso {{1}}, comparte este código con quien entrega: {{2}}." |
| `aviso_donante` | Avance a donantes | "Tu aporte {{1}} avanzó: {{2}}. Gracias por ayudar a reconstruir." |

*(La aprobación tarda de minutos a horas; los nombres exactos se configuran
luego en el backend.)*

## 7. Verificación final (la hace Claude)

Cuando termines los pasos 1–6, avísale a Claude en el chat. Claude:

1. Verifica que el GET de challenge quedó verificado y que los POST llegan
   firmados (`X-Hub-Signature-256` válida contra `META_APP_SECRET`).
2. Envía un mensaje de prueba desde tu número personal al número de la WABA
   y confirma el flujo completo (webhook → worker → respuesta del bot).
3. Revisa límites de la cuenta (tier de mensajería, calidad del número) y
   deja el canal en estado `ACTIVE`.

## Notas

- **Business verification:** para subir de tier de mensajería y salir del
  modo limitado, el Business Portfolio debe estar verificado (Configuración
  del negocio → Centro de seguridad → Verificación del negocio). Puede pedir
  documentos de la empresa; hazlo temprano.
- **Costos:** WhatsApp cobra por conversación según categoría (utility /
  marketing / service). Las respuestas dentro de la ventana de 24h iniciada
  por el usuario son las más económicas — el diseño del bot prioriza eso.
- **Sandbox mientras tanto:** en API Setup, Meta da un número de prueba y
  hasta 5 destinatarios de test; sirve para probar el flujo completo antes
  de registrar la línea productiva.
