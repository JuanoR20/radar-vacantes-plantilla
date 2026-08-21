# Radar de vacantes — plantilla

> **¿Acabas de copiar este repositorio? Empieza aquí.**
>
> 1. Abre `config.yaml` y pon tu usuario de GitHub en `notificar_a`.
> 2. Cambia las empresas. Para cada una:
>    `python3 radar.py --descubrir https://www.empresa.com/careers`
> 3. Cambia los filtros y los casos de `pruebas:`.
> 4. Sigue *Puesta en marcha*, aquí abajo.
>
> El código no se toca nunca. Todo lo tuyo vive en `config.yaml`.
> La sección **Cómo lo replica otra persona** lo explica con más detalle.

Revisa las páginas de carreras de un conjunto de empresas tres veces al día y abre un
issue en este repositorio cuando aparece un puesto nuevo que pasa los filtros.
GitHub manda el correo solo. Costo cero, sin servidores, sin dejar la máquina prendida.

**El producto es un correo. Todo lo demás es infraestructura.**

---

## Los archivos

| Archivo | Qué es | ¿Lo tocás? |
|---|---|---|
| `config.yaml` | Empresas y filtros | **Sí**, es lo único |
| `radar.py` | Todo el trabajo | Solo para agregar una plataforma nueva |
| `state.json` | Qué puestos ya se vieron | Nunca, lo maneja el script |
| `.github/workflows/radar.yml` | El horario | Solo para cambiar la hora |

---

## Puesta en marcha

**1. Repositorio privado.** Una búsqueda de trabajo no conviene que sea pública.

```bash
gh repo create radar-vacantes --private --source=. --push
```

**2. Permisos de escritura para Actions.** En *Settings → Actions → General →
Workflow permissions*, elegir **Read and write permissions**.

> Este es el paso que más gente olvida y el que hace que la primera corrida falle
> sin razón aparente. El workflow necesita escribir `state.json` y abrir issues.

**3. Primera corrida manual.** En la pestaña *Actions → Radar de vacantes → Run
workflow*, modo `normal`.

Esa primera corrida registra la **línea base**: guarda el catálogo completo y no
notifica nada. Es lo correcto — si no, el primer correo traería 800 puestos.

**4. Probar que el aviso llega.** *Actions → Run workflow*, modo **`prueba`**. Consulta
las fuentes de verdad y abre un issue de ejemplo con puestos reales. No toca el
estado. Comprueba de una sola vez que los conectores, los filtros, el issue y el
correo funcionan. Cerrá el issue y listo.

**5. Listo.** A partir de ahí corre solo, lunes a viernes a las **06:55, 10:55 y
17:55 hora de Londres**, más el resumen de los lunes a las 08:55, y solo escribe
cuando hay algo.

Los minutos `:55` no son capricho: GitHub encola las tareas programadas y la hora en
punto es el peor momento posible, porque es cuando programa todo el mundo. Correr a
:55 reduce mucho el retraso. Aun así puede llegar con unos minutos de atraso — es
normal, no es un fallo.

**El correo está en inglés**; este README y `config.yaml` siguen en español.

Para marcar un issue como leído, cerralo. Queda el historial ordenado.

---

## Uso local

```bash
pip install -r requirements.txt
```

```bash
python3 radar.py --check
```

**Diagnóstico.** Consulta todas las fuentes, imprime cuántos puestos encontró cada
una y cuántos pasan los filtros, y **no guarda nada**. Es el modo para probar
cambios de configuración y para verificar que los conectores siguen vivos.

```bash
python3 radar.py --test
```

**Auto-test de filtros**, sin tocar la red. Cincuenta y dos casos reales sacados de las
páginas de las empresas, incluida la trampa de "Strategy Manager en Kyiv, Ukraine".
Corrélo después de editar las listas de filtros.

```bash
python3 radar.py --weekly
```

**Resumen semanal.** Todo lo que está abierto ahora y pasa los filtros, no solo lo
nuevo. Corre solo los lunes. No toca el estado. Ver *Detección de fallas silenciosas*
más abajo para entender por qué existe.

```bash
python3 radar.py --demo
```

**Aviso de prueba.** Consulta las fuentes de verdad y arma un reporte con un par de
puestos reales que pasan los filtros, marcado como prueba. No toca el estado. Es la
forma de comprobar que el correo llega sin esperar a que aparezca una vacante.

```bash
python3 radar.py --reset
```

**Reinicio.** Borra el estado y vuelve a marcar la línea base desde cero. Solo si
algo se corrompió.

---

## Cómo lo replica otra persona

Todo lo específico de una búsqueda vive en `config.yaml`. El código no se toca.

1. **Copiar el repositorio.** En GitHub, botón verde *Use this template* → *Create a
   new repository* → privado.
2. **Partir de la plantilla.** `cp config.ejemplo.yaml config.yaml`, o copiar y pegar
   su contenido. Trae empresas de ejemplo, filtros comentados y casos de prueba.
3. **Poner su usuario de GitHub** en `opciones.notificar_a`.
4. **Añadir sus empresas.** Para cada una:

   ```bash
   python3 radar.py --descubrir https://www.empresa.com/careers
   ```

   Dice qué plataforma usa, imprime las líneas listas para pegar, **verifica que
   responda** y muestra dos títulos de ejemplo. Si la empresa usa una plataforma
   ilegible, lo dice y ahorra la tarde.
5. **Ajustar los filtros** y sus casos en `pruebas:`. Después, `--test` (no toca la
   red) y `--check` (consulta todo sin guardar nada).
6. **Publicar** siguiendo *Puesta en marcha*, arriba.

Lo único que se hereda de esta búsqueda son los filtros de ejemplo. Todo lo demás
—las once plataformas, la detección de fallas silenciosas, el filtrado en origen—
funciona igual para cualquier sector y cualquier país.

Los casos de `pruebas:` viven en el config y no en el código a propósito: describen
una **política** —qué roles quiero— y cambian con cada persona. Si el config no trae
`pruebas:`, `--test` usa los del código, que son los de esta búsqueda.

---

## Agregar una empresa

Entrá a su página de carreras, abrí cualquier puesto y mirá a dónde apunta el botón
de aplicar:

| El botón apunta a… | `plataforma:` | Identificador |
|---|---|---|
| `boards.greenhouse.io/acme` | `greenhouse` | `board: acme` |
| `jobs.ashbyhq.com/acme` | `ashby` | `board: acme` |
| `jobs.lever.co/acme` | `lever` | `board: acme` |
| `acme.eightfold.ai/careers` | `eightfold` | `tenant: acme` + `dominio:` |
| `acme.wd5.myworkdayjobs.com/…/Acme_Careers` | `workday` | `tenant: acme` + `pod: wd5` + `site: Acme_Careers` |
| `jobs.smartrecruiters.com/Acme` | `smartrecruiters` | `board: Acme` |
| `apply.workable.com/acme` | `workable` | `board: acme` |
| `acme.bamboohr.com/careers` | `bamboohr` | `board: acme` |
| `acme.pinpointhq.com/…` | `pinpoint` | `board: acme` |
| `careers.acme.com` (Teamtailor) | `teamtailor` | `board: careers.acme.com` (dominio completo) |
| `candidate.hr-manager.net/…?customer=acme` | `hrmanager` | `board: acme` |
| `acme.fa.oraclecloud.com/hcmUI/…/sites/CX_1` | `oracle` | `host:` + `site: CX_1` |
| `amazon.jobs` | `amazon` | `pais: GBR` |

**El board se copia literal, nunca se adivina.** Ashby y SmartRecruiters distinguen
mayúsculas (`TaptapSend`, `Wise`) y a veces el identificador tiene puntos o guiones
que parecen un error y no lo son (`checkout.com`, `primer.io`, `iwoca.co.uk`,
`allica-bank`, `starling-bank`, `moneyboxapp`, `hebbia-ai`).

> **El error más peligroso de todos.** Adivinar el identificador puede dar con el
> board de OTRA empresa, que responde bien y se ve perfectamente sano. Pasó con
> Primer: `primer` es una empresa de educación de EE.UU. que contrata profesores en
> Florida; la fintech es `primer.io`. La detección de fallas silenciosas **no** cubre
> este caso, porque no hay ni error ni cero. Por eso `--check` imprime dos títulos de
> ejemplo de cada fuente: leerlos delata el problema al instante.

Si la página de carreras es un sitio propio sin rastro del ATS —pasó con
Checkout.com, Starling y Allica— abrí un puesto y mirá el enlace de "Apply", que
es el que delata la plataforma.

Son tres líneas en `config.yaml`. **No hay que programar nada** — los adaptadores son
por *plataforma*, no por empresa.

> **Una sola excepción: `palladium`.** Su ATS real es Cornerstone, que exige
> autenticación, así que el adaptador consulta el endpoint que usa su propia web.
> Es código para una única empresa y por lo tanto la parte del proyecto con más
> probabilidad de romperse; la red que lo cubre es la detección de fallas
> silenciosas. Si aparece una segunda tentación de este tipo, conviene resistirla.

Greenhouse, Ashby y Lever cubren buena parte del fintech y las startups, así que la
mayoría de las empresas que quieras agregar ya están cubiertas.

Después de agregarla, corré `python3 radar.py --check` y verificá que no dé cero.

**Si un board está legítimamente vacío**, ponele `permitir_vacia: true`. Sin eso
avisaría "0 puestos" en cada corrida y el aviso de fallas dejaría de significar algo.
Se quita en cuanto la empresa publique algo.

**Plataformas que no se pueden leer.** No todas exponen una API. Casos concretos que
se intentaron y se descartaron:

- **Deel** (`jobs.deel.com/…`, la usa Klarna): sin API. Los puestos vienen dentro del
  HTML que genera Next.js en el servidor, en ~180 fragmentos.
- **Radancy** (la usa BlackRock): su endpoint devuelve JSON, pero el contenido es una
  cadena de HTML. Da igual: hay que parsear HTML.
- **JazzHR** (`*.applytojob.com`, la usa Antler): sin feed JSON ni RSS.
- **join.com** (la usa Bound): su API pública pide un id numérico de empresa que el
  sitio no publica por ningún lado.
- **Cornerstone** (`*.csod.com`, la usa Palladium): su API devuelve 401 sin
  autenticación.
- **Consider** (`careers.lightrock.com`): es el board de las empresas del portafolio
  de Lightrock, no de Lightrock. Sin API visible.
- **HiBob** (`*.careers.hibob.com`, la usa Zepz): aplicación Angular; todo lo que
  cuelga de `/api/` responde 406 a cualquier petición simple.
- **Factorial HR** (`*.factorialhr.com`, la usa Embat): sin API ni datos embebidos.
- **Revolut**: sus puestos sí están en JSON dentro de la página, pero el sitio
  responde **HTTP 403 a cualquier petición automatizada**. El navegador entra, un
  script no. No hay nada que hacer desde GitHub Actions.
- **Google**: su API pública de empleo (`careers.google.com/api/v3/search`) ya no
  existe; devuelve 404.
- **Avature** (`mycareer.hsbc.com`): no se le encontró endpoint público.

En todos estos casos leer los puestos obliga a parsear HTML, que es exactamente lo
que se rompe en silencio cuando rediseñan la página. La salida correcta es la alerta
nativa de la empresa, no un scraper.

**Si una empresa no aparece, fijate quién la compró.** Worldpay no salía por ningún
lado porque lo compró **Global Payments**, cuyo
Workday está bajo el tenant `tsys` (por TSYS, la empresa con la que se fusionaron).
Sus vacantes salen por esa fuente.

**Empresas grandes: filtrado en origen.** Workday y Oracle Fusion aceptan un filtro de
ubicación del lado del servidor, y los adaptadores lo usan con los términos de
`ubicaciones_origen`. No hay identificadores codificados a mano: se leen de las
facetas que la propia respuesta publica, así que una oficina nueva aparece sola.

| | Catálogo | Con filtro UK | Peticiones |
|---|---:|---:|---|
| JPMorgan Chase | 7.427 | 659 | 372 → 4 |
| Mastercard | 1.141 | 39 | 58 → 2 |
| Visa | 752 | 49 | 38 → 3 |
| Global Payments | 294 | 22 | 15 → 2 |

Sin esto, Visa y Mastercard llevaban la corrida a más de diez minutos. Con esto, 57
fuentes tardan menos que 44 sin él.

Dos trampas que solo se ven probando: en Workday el parámetro **no** se llama igual en
todas las empresas —unas exponen `locationCountry` y otras `locations`, y mandar los
identificadores bajo el nombre equivocado devuelve HTTP 502— y en Oracle, sin
`expand=requisitionList…` la respuesta trae el total pero la lista vacía.

**Oracle recorta las facetas a diez ubicaciones**, así que en empresas con poca
presencia en Reino Unido no aparece ninguna británica: AMEX publica 406 puestos y
ninguna de sus diez principales es de acá. En ese caso se trae el catálogo entero.
Por eso el adaptador **solo filtra cuando aparece la entrada del país completo**:
quedarse con una ciudad suelta dejaría fuera el resto del país en silencio.

Consecuencia práctica: la columna «Roles» no significa lo mismo en todas las fuentes.
Donde el filtro en origen se aplicó son los puestos de Reino Unido; donde no, es el
catálogo global. AMEX aparece con 406 y Mastercard con 39, pero en Reino Unido AMEX
tiene 17 y Mastercard 39.

Se verificó que no pierde puestos: en Remitly y en Mastercard, filtrar en origen
devuelve exactamente los mismos que encontraría el filtro local sobre el catálogo
completo. Y si no
reconoce ninguna ubicación, se trae todo: lento, pero nunca miente.

**Si el sitio solo carga con JavaScript**, no construyas un scraper con navegador
simulado: ahí se va la mayor parte del mantenimiento futuro por poco valor.
Suscribite a la alerta nativa del portal de esa empresa y seguí.

---

## Qué muestra cada aviso

```
- **[Senior Manager, Strategy & Operations CEO Office](…)** 🔥
  - UK - London · Strategy & Operations · 2 days ago
  - matched: strategy, operations
```

**La antigüedad** es la fecha real de publicación, no la de detección. No son lo
mismo: al agregar una empresa nueva, su catálogo entero llega como novedad aunque
lleve meses abierto. El 🔥 marca los de 3 días o menos, que son los que conviene
mirar primero.

**El departamento** aparece cuando la plataforma lo publica: sí en Ashby, Lever,
Eightfold, SmartRecruiters y Workable; no en Greenhouse ni Workday. Si una empresa
manda el mismo valor en todos sus puestos (Checkout.com pone `All cost centres` en
el 99%), se descarta por no aportar nada.

**«Entró por»** dice qué término de `titulos_incluir` hizo entrar al puesto. Es para
poder afinar los filtros sin leer código: si tres avisos seguidos dicen
`matched: growth` y son irrelevantes, ya sabés qué término tocar. Va sin comillas
invertidas a propósito: el formato de código de Markdown usa otra tipografía y otro
tamaño, y desentonaba con los demás sub-puntos.

Dentro de cada empresa, los más recientes van arriba.

## Cómo funciona

### Identidad de los puestos

Cada puesto necesita una identidad estable entre corridas. Si esto está mal, el
sistema genera alertas falsas todos los días y se vuelve inútil.

La clave es `empresa:id_de_la_plataforma`. Las cuatro plataformas dan IDs, así que
ese es el caso normal. Como respaldo hay una huella SHA-256 de título + ubicación
normalizados (minúsculas, sin acentos, espacios colapsados).

> La huella **tiene que** usar un hash criptográfico, no la función `hash()` de
> Python: Python aleatoriza el hash de strings en cada arranque del proceso, así que
> la misma huella daría un valor distinto en cada corrida y todo aparecería como
> nuevo siempre. No falla ruidosamente — solo inunda de alertas falsas.

### Los filtros van al final, nunca al principio

Se guarda **todo** lo que devuelven las fuentes y se filtra solo al armar el reporte.

Si se filtrara antes de guardar, el día que amplíes un filtro decenas de puestos que
existían desde hace meses aparecerían de golpe como "nuevos" — el sistema se vuelve
inusable justo cuando lo estás ajustando. Guardando todo, cambiar filtros es gratis.
Cuesta unos pocos megabytes de JSON.

### Cómo se comparan los términos

Dos modos distintos, y los dos hacen falta:

- **Ubicaciones: palabra completa.** Si "uk" se buscara como subcadena, coincidiría
  con "Ukraine" y con cualquier ciudad ucraniana. Un puesto en Kyiv aparecería como
  si fuera de Reino Unido.
- **Títulos: prefijo de palabra.** El término tiene que empezar una palabra pero
  puede continuar. Así "ops" encuentra "Operations" y "partnerships" encuentra
  "Partnerships Manager". Con palabra completa estricta se perdería la mitad de los
  puestos relevantes.

`titulos_excluir` tiene prioridad sobre `titulos_incluir`.

`titulos_practicas` clasifica: decide si un puesto aparece bajo *💼 full time* o bajo
*🎓 prácticas / internships*, para que una práctica nunca se mezcle con los puestos
senior.

Y activa una valla extra. `practicas_requieren` (hoy: `mba`) exige que una práctica
mencione además alguno de esos términos para pasar. Con un MBA en curso, un graduate
scheme o un internship común son un paso atrás, así que se descartan aunque el rol
encaje. Dejar la lista vacía acepta cualquier práctica.

### Detección de fallas silenciosas

El riesgo real no es que el sistema falle con un error visible. Es que **falle en
silencio**: la web de una empresa cambia, el conector deja de encontrar puestos, y
"no hay alertas" se lee como "no hay vacantes nuevas". Un sistema así es **peor que
no tener nada**, porque da falsa tranquilidad y hace que dejes de revisar a mano.

La protección:

- El estado guarda cuántos puestos devolvió cada fuente. Si una fuente devuelve
  **cero**, **falla**, o sufre una **caída brusca** (>70% menos que la corrida
  anterior, configurable), se reporta como problema explícito — no como silencio.
- **Cada adaptador está aislado.** Si una fuente se cae, las demás siguen reportando
  normal. Un error en una fuente no tumba la corrida entera.
- **Cada fuente tiene un presupuesto de tiempo** (`limite_segundos_por_fuente`, 150 s
  por defecto, reintentos incluidos). Sin él, una web inestable alarga la corrida
  entera: el Eightfold de PayPal llegó a consumir seis minutos por intento antes de
  rendirse.
- **Solo se limpian del estado las fuentes que respondieron bien.** Si una fuente
  falló y le borráramos los puestos, la próxima corrida sana reportaría el catálogo
  entero como nuevo.

Cuando hay un problema, el issue se abre igual aunque no haya vacantes nuevas.

**El aviso sin novedades** (`avisar_sin_novedades`) cierra el círculo día a día:
llega aunque no haya nada, con la tabla de estado de las 31 fuentes, para que una
mañana en silencio no se pueda confundir con una mañana rota. Cuesta volumen — en
`siempre` son ~15 correos por semana — así que hay un punto medio, `manana`, que lo
manda solo en la primera corrida del día. Con `nunca` se vuelve al comportamiento
original de escribir solo cuando hay algo.

**Y el resumen semanal de los lunes cierra el círculo.** Llega siempre, incluso con
cero resultados. Sin él, una semana sin correos es ambigua: puede ser "no hay nada"
o "se rompió y nadie se enteró". Con él, el silencio total es imposible — y de paso
rescata lo que se pasó por alto al cerrar un aviso sin leerlo.

### Estado como archivo commiteado

`state.json` va en el repositorio, no en el caché de Actions: el caché expira a los 7
días y perderlo significa recibir el catálogo entero como novedad. Como archivo hay
historial y se puede recuperar. De yapa, commitear en cada corrida mantiene el repo
activo y evita que GitHub desactive los workflows programados a los 60 días.

---

## Notas sobre las plataformas

**Greenhouse** — `boards-api.greenhouse.io/v1/boards/{board}/jobs?content=false`.
API pública documentada, una sola petición.

**Ashby** — `api.ashbyhq.com/posting-api/job-board/{board}`. API pública, una sola
petición. La respuesta incluye la descripción completa de cada puesto, así que pesa
varios MB; el adaptador se queda solo con los campos que usa.

> Airwallex tiene además un WordPress con Elementor en `careers.airwallex.com` con 83
> páginas de paginación y un parámetro interno (`e-page-…`) que se rompe si rediseñan
> la página. Ir directo a Ashby evita todo eso. **No hay que scrapear HTML.**

**Lever** — `api.lever.co/v0/postings/{board}?mode=json`. Incluido aunque hoy no se
use: cubre otra porción grande del mercado de startups y es casi idéntico a
Greenhouse.

**Workday** — POST a `{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`
con `{"appliedFacets":{},"limit":20,"offset":N,"searchText":""}`. Dos trampas que
solo aparecen probando contra el servidor real:

- **`total` viene únicamente en la primera página**; las siguientes devuelven `0`.
  Si se lee en cada vuelta, el bucle corta en la página 2 y se pierde el 75% del
  catálogo **en silencio**.
- **`limit` mayor a 20 devuelve HTTP 400.**

Además, algunos puestos traen `"2 Locations"` en vez del lugar, y el filtro de
ubicación los descartaría sin que nadie se entere. Para esos —14 de 158 en Remitly—
el adaptador consulta el detalle del puesto, que sí trae las ubicaciones reales y la
fecha de publicación de verdad.

**SmartRecruiters** — `api.smartrecruiters.com/v1/companies/{board}/postings`,
paginado de a 100. API pública documentada. El campo `department` suele venir vacío;
el adaptador cae a `function` ("Product Management", "Analyst"), que informa menos
pero algo dice.

**Workable** — `apply.workable.com/api/v1/widget/accounts/{board}?details=true`. Una
sola petición devuelve el board entero, con departamento y ubicaciones completas.

**Eightfold** — el endpoint legacy `/api/apply/v2/jobs` devuelve **403 "Not
authorized for PCSX"** en los tenants migrados al producto nuevo, PayPal incluido. El
que funciona es:

```
https://{tenant}.eightfold.ai/api/pcsx/search?domain={dominio}&sort_by=timestamp&start=0&num=10
```

Ignora `num > 10`, así que hay que paginar de a 10 leyendo `data.count`. La
paginación devuelve algún duplicado en los bordes; el adaptador deduplica por id.

Este es el conector más frágil de los cuatro, porque no está documentado
oficialmente. Si algún día se rompe y no se arregla rápido, la salida correcta es
sacar PayPal de `config.yaml` y suscribirse a la alerta nativa de su portal, que
tiene cero mantenimiento.

---

## Volumen esperado

Sin filtros esto son cientos de alertas por semana y el sistema se silencia en dos
semanas. Con los filtros actuales, del catálogo completo (~7000 puestos entre las 59
empresas activas) pasan unos 246 — y esas son las que **ya existen**, las que ves en el
resumen de los lunes. Lo que llega por correo entre semana son solo las **altas
nuevas**: unas pocas por semana.

Si llegan demasiadas o muy pocas, ajustá las listas y volvé a correr `--check`. Como
los filtros se aplican al mostrar, ajustar no rompe nada ni genera alertas
retroactivas.

---

## Qué NO hay acá, a propósito

Base de datos (un JSON alcanza). Interfaz web o dashboard (el producto es un correo).
Detección de puestos que se cierran (duplica la complejidad del estado y no sirve
para nada práctico). Reintentos sofisticados, proxies o user-agents falsos (son ~20
peticiones dos veces al día contra APIs públicas de lectura). Scrapers con navegador
simulado (la principal fuente de mantenimiento y de fallas silenciosas).
