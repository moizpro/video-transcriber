# Video Transcriber

Convierte vídeos a subtítulos `.srt` usando Whisper AI ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)), sin enviar nada a Internet: todo el reconocimiento de voz se ejecuta en tu propio ordenador.

Hay **dos formas de usarlo**, y ambas comparten el mismo motor de transcripción:

| | App de escritorio | Interfaz web |
|---|---|---|
| Archivo de arranque | `transcriber_app.py` | `web/server.py` (vía `run_web.sh`) |
| Cómo se abre | Doble clic en el `.app`, o `python3 transcriber_app.py` | Navegador, en `http://localhost:8000` |
| Para quién | Solo tú, en este Mac | Tú (o quien tenga la contraseña), desde cualquier sitio si la despliegas |

---

## Guía rápida — "Quiero usarlo ya"

### Opción A: app de escritorio (como siempre)
Doble clic en `Video Transcriber.app` (Escritorio), o:
```bash
python3 transcriber_app.py
```

### Opción B: interfaz web, en este Mac
```bash
cd ~/Documents/video_transcriber
bash run_web.sh
```
Espera a ver `Application startup complete`, abre **http://localhost:8000** en el navegador, y entra con la contraseña que hayas puesto en el campo `TRANSCRIBER_PASSWORD` de tu archivo `.env` (no se sube a git, así que solo tú la tienes). Dejas la terminal abierta mientras la usas; `Ctrl+C` para apagarla.

Esto funciona igual **aunque hayas apagado el ordenador** desde la última vez — no hace falta instalar nada de nuevo, todo quedó guardado. Más detalles y solución de problemas en [la sección de más abajo](#guía-de-uso-detallada).

---

## 1. Qué se ha hecho y por qué

Antes, este proyecto era solo una app de escritorio (`transcriber_app.py`, con interfaz `customtkinter`). Para añadir una versión web **sin duplicar la lógica de transcripción**, se separó el código en tres bloques:

```
video_transcriber/
├── transcriber_core.py      ← el "motor": todo lo que NO depende de la interfaz
├── transcriber_app.py        ← la interfaz de escritorio (usa el motor)
└── web/                       ← la interfaz web (usa el mismo motor)
    ├── config.py               (lee la configuración: contraseña, límites…)
    ├── auth.py                  (login, sesiones, protección anti fuerza bruta)
    ├── jobs.py                   (cola de trabajos, un vídeo a la vez)
    ├── server.py                  (las "rutas" — qué hace cada URL de la web)
    └── static/                    (lo que ve el navegador: HTML, CSS, JS)
```

La idea clave: **`transcriber_core.py` no sabe que existen ni la ventana de escritorio ni el navegador.** Solo sabe transcribir vídeos. Tanto `transcriber_app.py` como `web/jobs.py` lo llaman y le dicen "avísame del progreso así" y "para si cancelo". Esto significa que si algún día mejoras el motor de transcripción (por ejemplo, cambias de modelo o añades un formato de subtítulos nuevo), lo haces **una sola vez** y las dos interfaces se benefician.

Como la web va a estar accesible desde Internet (no solo en tu Mac), no basta con que "funcione" — tiene que protegerse de mal uso: contraseña, límite de tamaño de subida, que nadie pueda ver los vídeos de otra persona, y que los archivos no se queden acumulados en el disco para siempre. Esos puntos se explican en la [sección de seguridad](#4-seguridad-por-qué-se-hizo-así).

---

## 2. El motor compartido: `transcriber_core.py`

Este archivo es el corazón de todo. No tiene ninguna dependencia de interfaz (ni `tkinter` ni `fastapi`) — solo sabe de vídeos, modelos Whisper y archivos `.srt`.

**Constantes** (al principio del archivo): la lista de modelos Whisper (`tiny`, `base`, `small`, `medium`, `large`), el modelo por defecto (`medium`), el diccionario de idiomas, y las extensiones de vídeo soportadas. Tanto la app de escritorio como la web leen estas mismas listas, así que si añades un idioma aquí, aparece automáticamente en los dos sitios.

**Funciones auxiliares:**
- `segments_to_srt(segments)` — Whisper transcribe el audio y devuelve una lista de "segmentos" (fragmentos de texto con su minuto de inicio y fin). Esta función los convierte al formato de texto `.srt` estándar:
  ```
  1
  00:00:00,000 --> 00:00:01,500
  hola

  2
  00:00:01,500 --> 00:00:03,000
  mundo
  ```
- `ffmpeg_available()` — comprueba que `ffmpeg` (el programa que lee el audio de los vídeos) está instalado, igual que hacía siempre la app de escritorio.
- `classify_error(exc)` — cuando algo falla (modelo sin descargar, vídeo sin audio, etc.), traduce el error técnico de Python a un mensaje en español que tiene sentido para una persona.

**La función más importante — `transcribe_video(...)`:**

```python
transcribe_video(
    video_path,        # ruta al vídeo
    model_size,         # "tiny", "base", "small", "medium" o "large"
    lang_code,           # "es", "en"… o None para autodetectar
    cancel_event=...,     # una "bandera" que, si se activa, para la transcripción
    on_progress=...,       # una función a la que se le va avisando del progreso
)
```

Por dentro: carga el modelo Whisper, le pasa el vídeo, y por cada fragmento de audio que Whisper va transcribiendo, llama a `on_progress(...)` con el texto de estado actual, el porcentaje completado y el tiempo estimado restante. Si en algún momento `cancel_event` está activado, se interrumpe lanzando una señal especial (`TranscriptionCancelled`) en vez de simplemente devolver un valor — así quien la llama puede reaccionar como quiera (la app de escritorio muestra "cancelado", la web marca el trabajo como `cancelled`).

Esta función es **bloqueante**: tarda lo que tarde Whisper en transcribir (segundos a minutos), así que tanto la app de escritorio como la web la ejecutan siempre en un hilo secundario, nunca en el hilo principal — si no, la ventana o el servidor se quedarían congelados mientras transcribe.

---

## 3. La app de escritorio: `transcriber_app.py`

No ha cambiado de comportamiento — sigue siendo la misma ventana de siempre. Lo único que cambió es que ahora, en vez de tener su propia copia de las constantes y la lógica de transcripción, importa todo desde `transcriber_core.py`:

```python
from transcriber_core import (
    WHISPER_MODELS, LANGUAGES, ...,
    transcribe_video, classify_error,
)
```

Y su `_transcription_worker` (el código que corre en el hilo secundario cuando pulsas "Transcribir") ahora es mucho más corto: simplemente llama a `transcribe_video(...)` y traduce su resultado o sus errores a las funciones que ya existían para actualizar la ventana (`_on_success`, `_on_error`, `_on_cancelled`).

---

## 4. La interfaz web: cómo funciona por dentro

### 4.1. ¿Qué es FastAPI y por qué se usa?

FastAPI es una librería de Python para crear servidores web: tú defines funciones de Python normales, y FastAPI las convierte en "lo que pasa cuando alguien visita esta URL". Por ejemplo:

```python
@app.get("/api/config")
async def get_config(...):
    return {"models": [...], "languages": {...}}
```

significa "cuando el navegador pida `GET /api/config`, ejecuta esta función y devuelve lo que retorne como JSON". El navegador (en `app.js`) hace estas peticiones con `fetch(...)`.

### 4.2. El flujo completo de una transcripción

```
Navegador                          Servidor (FastAPI)
   │                                     │
   │── POST /api/login (contraseña) ───▶│  comprueba la contraseña,
   │◀── cookie de sesión ────────────────│  crea una sesión
   │                                     │
   │── GET /api/config ─────────────────▶│  devuelve modelos/idiomas
   │◀── {models, languages, ...} ────────│  (para rellenar los <select>)
   │                                     │
   │── POST /api/jobs (el vídeo) ───────▶│  guarda el vídeo en disco,
   │◀── {id: "abc123"} ──────────────────│  lo mete en la cola
   │                                     │     ↓
   │                                     │  un hilo de fondo lo coge
   │                                     │  de la cola y llama a
   │                                     │  transcribe_video(...)
   │                                     │
   │── GET /api/jobs/abc123 (cada 1.5s)─▶│  ¿cómo va? (se repite mientras
   │◀── {status: "processing", 45%} ─────│   dura la transcripción)
   │                                     │
   │── GET /api/jobs/abc123 ────────────▶│
   │◀── {status: "done", download_url}──│
   │                                     │
   │── GET /api/jobs/abc123/download ───▶│  envía el archivo .srt
   │◀── (el archivo .srt) ────────────────│
```

El navegador nunca se queda "esperando" a que termine la transcripción en una sola petición (eso sería frágil si dura varios minutos) — sube el vídeo, recibe un identificador, y va preguntando "¿cómo vas?" cada 1.5 segundos. Esto se llama *polling* y es la técnica más simple posible para este caso; no hace falta nada más sofisticado.

### 4.3. `web/config.py` — la configuración

Lee todo lo que se puede ajustar desde variables de entorno (`TRANSCRIBER_PASSWORD`, `SECRET_KEY`, límite de subida, etc.) en un solo sitio. Si falta `TRANSCRIBER_PASSWORD` o `SECRET_KEY`, el programa **se niega a arrancar** — mejor un error claro al iniciar que una web pública sin contraseña por descuido.

### 4.4. `web/auth.py` — quién puede entrar

No hay "usuarios" ni base de datos: solo una contraseña compartida (`TRANSCRIBER_PASSWORD`). Cuando alguien la introduce correctamente:
1. Se compara con `hmac.compare_digest(...)` en vez de con `==`. La diferencia: comparar con `==` puede tardar un poquito menos si las primeras letras ya no coinciden, y ese "poquito menos" es medible — en teoría, alguien podría usarlo para adivinar la contraseña letra a letra midiendo tiempos de respuesta. `compare_digest` siempre tarda lo mismo, así que no hay nada que medir.
2. Se le da una *cookie de sesión* — un identificador firmado que el navegador guarda y reenvía en cada petición, para no tener que escribir la contraseña en cada clic.
3. Se le asigna un `session_id` propio (un código aleatorio). Esto es lo que permite que, si dos personas distintas usan la misma contraseña a la vez, **ninguna vea los vídeos ni las transcripciones de la otra** — cada trabajo queda "etiquetado" con el `session_id` de quien lo creó.

También hay un límite de 5 intentos de contraseña fallidos cada 5 minutos por dirección IP, para que alguien no pueda intentar adivinarla a fuerza bruta.

### 4.5. `web/jobs.py` — la cola de transcripciones

Aquí vive el `JobManager`. La idea central: **solo se transcribe un vídeo a la vez en todo el servidor**, exactamente igual que en la app de escritorio (donde el botón se desactiva mientras procesa). Si dos personas suben un vídeo casi a la vez, la segunda simplemente espera en la cola — no se rechaza ni se pierde, solo se pone en fila.

Esto se consigue con un único hilo de fondo (`_run_worker`) que coge trabajos de una cola (`queue.Queue`) de uno en uno, llama a `transcribe_video(...)`, y va guardando el progreso en un diccionario en memoria. Un trabajo (`Job`) pasa por estos estados:

```
queued → processing → done
                    ↘ error
                    ↘ cancelled
```

Cuando un trabajo termina (de cualquiera de las tres formas), el **vídeo original se borra inmediatamente** del disco del servidor — solo se conserva el `.srt` resultante. Y cada pocos minutos, una tarea de limpieza borra los trabajos terminados hace más de una hora (`JOB_TTL_MINUTES` en `.env`) por si alguien nunca vuelve a descargar su resultado. Así el disco del servidor no se va llenando con el tiempo.

### 4.6. `web/server.py` — las rutas de la web

Conecta todo lo anterior. Las rutas más importantes:

| Ruta | Qué hace |
|---|---|
| `POST /api/login` | Comprueba la contraseña, crea la sesión |
| `GET /api/config` | Devuelve modelos/idiomas/extensiones válidas (para los desplegables) |
| `POST /api/jobs` | Recibe el vídeo subido y lo mete en la cola |
| `GET /api/jobs/{id}` | "¿Cómo va este trabajo?" |
| `POST /api/jobs/{id}/cancel` | Cancela un trabajo en marcha |
| `GET /api/jobs/{id}/download` | Descarga el `.srt` ya generado |

Un detalle importante en `POST /api/jobs`: el vídeo subido se escribe a disco **en trozos de 1 MB**, nunca de golpe. Si lo leyera todo de una vez en memoria, subir un vídeo de varios GB podría agotar la memoria del servidor. Mientras escribe, comprueba que no se pase del límite configurado (`MAX_UPLOAD_MB`, 2 GB por defecto) — si se pasa, para y borra lo que llevaba escrito.

### 4.7. `web/static/` — lo que ve el navegador

Tres archivos sencillos, sin frameworks ni paso de compilación (no hace falta `npm`, ni nada parecido):
- **`index.html`** — la estructura de la página: la pantalla de login y la pantalla principal (oculta una mientras se ve la otra).
- **`style.css`** — el aspecto visual (tema oscuro, tarjeta centrada).
- **`app.js`** — toda la interactividad: enviar el login, arrastrar/soltar el vídeo, subirlo mostrando una barra de progreso de subida, y preguntar cada 1.5 segundos por el estado del trabajo (la función `pollStatus()`).

---

## 5. Seguridad: por qué se hizo así

Como esta web puede acabar siendo accesible desde Internet (no solo en tu Mac), se tomaron estas decisiones a propósito:

- **Contraseña obligatoria** — el servidor no arranca sin ella. Sin esto, cualquiera con la URL podría usar tu servidor (y tu CPU) para transcribir sus propios vídeos.
- **Aislamiento entre sesiones** — cada persona solo ve sus propios trabajos, aunque todas compartan la misma contraseña.
- **Límite de tamaño de subida** — evita que alguien llene el disco del servidor subiendo archivos enormes repetidamente.
- **Límite de intentos de login** — evita que alguien intente adivinar la contraseña probando miles de combinaciones.
- **Borrado automático de archivos** — los vídeos no se conservan más de lo necesario, y los resultados caducan al cabo de una hora si nadie los descarga.
- **Una transcripción a la vez** — evita que alguien sature la máquina lanzando muchos modelos `large` simultáneamente.

Lo que **no** se ha construido, a propósito, por ser innecesario a este tamaño: cuentas de usuario, base de datos, colas distribuidas (Celery/Redis), WebSockets. Todo eso añadiría complejidad sin un beneficio real aquí.

---

## Guía de uso detallada

### Arrancar la web en este Mac (lo normal, día a día)

```bash
cd ~/Documents/video_transcriber
bash run_web.sh
```

Este script:
1. Lee la contraseña y demás configuración del archivo `.env` (ya creado, no hace falta tocarlo).
2. Arranca el servidor en `http://localhost:8000`.

Abre esa dirección en el navegador, escribe la contraseña (la que está en `.env`, campo `TRANSCRIBER_PASSWORD`), y ya puedes subir vídeos. Para detenerlo, vuelve a la terminal y pulsa `Ctrl+C`.

**Esto seguirá funcionando aunque apagues y enciendas el ordenador** — no es necesario reinstalar nada ni repetir ningún paso de configuración, porque:
- Las dependencias de Python (`fastapi`, `uvicorn`, etc.) ya están instaladas en tu sistema.
- El archivo `.env` con la contraseña y la clave de sesión ya existe y no se borra.
- El modelo Whisper que uses se descarga la primera vez y queda guardado en `~/.cache/huggingface/hub/` — no se vuelve a descargar.

Si alguna vez cambias de ordenador o reinstalas Python desde cero, sí tendrías que repetir:
```bash
pip3 install -r requirements.txt
cp .env.example .env   # y rellenar TRANSCRIBER_PASSWORD y SECRET_KEY
```

### ¿Y si quiero acceder desde el móvil u otro ordenador de casa?

`run_web.sh` ya arranca el servidor escuchando en todas las interfaces de red (`--host 0.0.0.0`), no solo en `localhost`. Eso significa que, mientras tu Mac esté encendido y en la misma red Wi-Fi, puedes entrar desde otro dispositivo usando la IP local de tu Mac en vez de `localhost`, por ejemplo `http://192.168.1.23:8000` (puedes ver tu IP local en Preferencias del Sistema → Red). Fuera de tu red Wi-Fi (desde fuera de casa) no funcionará todavía — eso requiere desplegarlo en un servidor de Internet, que es el siguiente paso si llegas a necesitarlo.

### Desplegar en un servidor de Internet (cuando lo decidas)

El proyecto ya incluye un `Dockerfile` listo para eso. Resumen:
```bash
docker build -t video-transcriber .
docker run -p 8000:8000 \
  -e TRANSCRIBER_PASSWORD=una_contraseña_nueva_y_distinta \
  -e SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") \
  -e SESSION_HTTPS_ONLY=true \
  -v transcriber_data:/data/jobs \
  video-transcriber
```
Cuando llegue el momento, usa una contraseña distinta a la que tengas configurada para uso local, y pon `SESSION_HTTPS_ONLY=true` ya que un servidor real debería ir siempre detrás de HTTPS. La plataforma concreta (VPS, Railway, Render…) la decides tú cuando llegue el momento — el `Dockerfile` no asume ninguna en particular.

### Problemas comunes

- **"Falta la variable de entorno obligatoria..."** — el archivo `.env` no existe o le falta `TRANSCRIBER_PASSWORD`/`SECRET_KEY`. Copia `.env.example` a `.env` y rellénalo.
- **ffmpeg no está instalado** — instálalo con `brew install ffmpeg` (afecta tanto a la app de escritorio como a la web).
- **El puerto 8000 ya está en uso** — cambia `PORT=8000` por otro número en `.env`.
- **Quiero cambiar la contraseña** — edita `TRANSCRIBER_PASSWORD` en `.env` y reinicia `run_web.sh`. Quien ya tuviera una sesión abierta seguirá entrando hasta que cierre sesión o pase una semana (la sesión caduca sola), pero nadie nuevo podrá entrar con la contraseña vieja.
