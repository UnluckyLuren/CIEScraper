# Automatización y Migración de Eventos Institucionales - CIE CUCEI

Herramienta interna desarrollada para la Coordinación de Ingeniería en Computación / CIE del Centro Universitario de Ciencias Exactas e Ingenierías (CUCEI - UdeG). 

Este sistema automatiza la extracción de publicaciones desde la página oficial de Facebook de CUCEI, estructura la información, la traduce al inglés, clasifica su impacto en los Objetivos de Desarrollo Sostenible (ODS) y genera los bloques HTML con el formato requerido por el gestor de contenidos del portal institucional de eventos de CUCEI.

---

## El Problema

Gran parte de las actividades académicas, talleres, conferencias y campañas del centro universitario se difunden de manera inmediata en redes sociales (Facebook), pero registrarlas manualmente en el portal web oficial implicaba un trabajo repetitivo y lento:

1. Buscar publicación por publicación en el historial de Facebook.
2. Redactar una nota formal a partir del texto de la red social.
3. Traducir el título, resumen y cuerpo de la nota al inglés.
4. Identificar a qué Objetivo de Desarrollo Sostenible (ODS / SDG) aporta el evento.
5. Maquetar todo manualmente en etiquetas HTML compatibles con los campos del portal.

Inicialmente se probó una integración con servicios externos (Apify), pero el costo por ejecución y los límites de créditos hacían inviable procesar cientos de eventos históricos. Por ello, se construyó un microservicio propio alojado localmente.

---

## Arquitectura de la Solución

El proyecto se divide en dos componentes principales que corren sobre un servidor propio (Linux / Docker / CasaOS):

```text
+-----------------------+       POST /scrape        +-----------------------------+
|                       | ------------------------> |  Contenedor Scraper (API)   |
|     Flujo en n8n      |                           |  FastAPI + Playwright       |
|       (Local)         | <------------------------ |  Extracción DOM + GraphQL   |
+-----------------------+    JSON (Posts + Fechas)  +-----------------------------+
           |
           | Lotes de 5 registros
           v
+-----------------------+
|   AWS Bedrock (LLM)   |
|   Llama 3.3 70B       |
|   Redacción ES/EN     |
+-----------------------+
           |
           v
+-----------------------+
|   Catálogo Web HTML   |
|   Buscador, Filtros   |
|   y Copy-to-Clipboard |
+-----------------------+
```

### 1. Microservicio de Extracción (`fb-cucei-scraper`)
Un contenedor Docker con **Python, FastAPI y Playwright (Chromium Headless)** que reemplaza a las herramientas de pago.
* **Inyección y sanitización de cookies:** Limpia automáticamente cookies exportadas del navegador para mantener una sesión válida y evitar el bloqueo de scroll de Facebook.
* **Fechas exactas sin estimaciones:** Intercepta las respuestas internas de red (`GraphQL` / `creation_time`) y analiza atributos del DOM para obtener el día exacto de la publicación original.
* **Filtrado flexible:** Permite extraer una cantidad fija de publicaciones recientes (`max_posts`) o barrer un rango de fechas completo (`fecha_inicio` a `fecha_fin`).
* **Optimización de recursos:** Bloquea la carga de video y fuentes tipográficas pesadas para operar de forma rápida en servidores con recursos moderados.

### 2. Pipeline de Procesamiento y Redacción (`n8n` + `AWS Bedrock`)
El flujo automatizado en **n8n** recibe el arreglo JSON directamente desde el contenedor y ejecuta los siguientes pasos:
* **Procesamiento por lotes (`SplitInBatches`):** Agrupa las publicaciones de 5 en 5 para optimizar el consumo de tokens y respetar los límites de tasa de la API.
* **Estandarización Bilingüe (Llama 3.3 70B):** A partir del texto crudo de Facebook, el modelo redacta la nota institucional en tres párrafos (español e inglés), asigna la categoría del evento, estima la asistencia según el tipo de actividad y determina los ODS correspondientes.
* **Generación de Catálogo Interactivo:** Empaqueta todos los resultados en un archivo `.html` autocontenido que sirve como panel de control para el equipo del CIE.

---

## Características del Catálogo HTML Generado

El archivo final (`Eventos_CUCEI_2025_Bloques_HTML.html`) incluye una interfaz de trabajo con:
* **Filtros dinámicos:** Búsqueda por nombre, rango de fechas, dependencia organizadora, clasificación y ODS.
* **Control de progreso (`localStorage`):** Botón para marcar eventos como "Ya usados", los cuales se atenúan visualmente y se guardan en el navegador para no duplicar cargas en el portal de CUCEI.
* **Copiado en un clic:** Botón directo para copiar el bloque HTML limpio al portapapeles y pegarlo en el editor de Drupal.
* **Vista previa renderizada:** Desplegable en cada tarjeta para revisar cómo se verá la nota y la fotografía antes de publicarla.

---

## Instalación y Despliegue

### Requisitos Previos
* Docker y Docker Compose instalados.
* Instancia de **n8n** (Community Edition o superior).
* Credenciales de AWS con acceso a Amazon Bedrock (o cualquier proveedor LLM compatible en n8n).
* Archivo `fb_cookies.json` exportado de una cuenta de Facebook activa (para evitar límites de lectura en el feed).

### 1. Levantar el contenedor del Scraper
Clonar el repositorio y entrar al directorio del servicio:

```bash
git clone https://github.com/tu-usuario/cucei-cie-fb-migrator.git
cd cucei-cie-fb-migrator/scraper
```

Colocar el archivo de cookies en la carpeta correspondiente:
```bash
mkdir -p cookies
cp /ruta/a/las/fb_cookies.json ./cookies/fb_cookies.json
```

Construir e iniciar el contenedor:
```bash
sudo docker compose up -d --build
```
El servicio se quedará escuchando en el puerto `8085`.

### 2. Uso de la API del Scraper
Puedes probar la extracción directamente por terminal o desde el nodo `HTTP Request` de n8n enviando un `POST` a `/scrape-sync`:

**Ejemplo por número de publicaciones:**
```json
{
  "page_url": "https://www.facebook.com/udegcucei",
  "max_posts": 15,
  "dependencia_default": "Coordinacion de Computacion - CUCEI"
}
```

**Ejemplo por rango de fechas (año completo):**
```json
{
  "page_url": "https://www.facebook.com/udegcucei",
  "max_posts": 0,
  "fecha_inicio": "2025-01-01",
  "fecha_fin": "2025-12-31",
  "dependencia_default": "Coordinacion de Computacion - CUCEI"
}
```

### 3. Configurar el flujo en n8n
1. Importa el archivo `workflows/n8n_workflow_cie.json` en tu instancia de n8n.
2. Configura tus credenciales de AWS Bedrock en el nodo del modelo de lenguaje.
3. En el nodo `HTTP Request`, apunta la URL a la IP local de tu servidor (`http://La_IP:8085/scrape-sync`).
4. Ejecuta el flujo y descarga el catálogo HTML resultante.

**Desarrollado para la Coordinación de Ingeniería en Computación y para el Centro de Innovación y Emprendimiento (CIE) - CUCEI, Universidad de Guadalajara.**
