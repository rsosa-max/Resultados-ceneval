# Portal de resultados EGEL · ESEST

Página estática (GitHub Pages) donde cada egresado consulta sus resultados del EGEL
con su **folio + apellido paterno**, ve sus puntajes por sección y área, y descarga su
reporte oficial del Ceneval en PDF.

## Cómo protege los datos

- Cada registro (puntajes + PDF) está cifrado con AES‑256‑GCM. La llave se deriva
  del folio y el apellido paterno (PBKDF2, 250 000 iteraciones). El descifrado
  ocurre en el navegador de la persona.
- El repositorio no contiene nombres, puntajes ni PDFs legibles. Los archivos de
  `datos/r/` se nombran con un hash del folio.
- Si el apellido tiene varias palabras (p. ej. *Leal Isla*), se aceptan las
  variantes razonables (*Leal* o *Leal Isla*). Mayúsculas y acentos no importan.

**Límite a tener en cuenta:** quien tenga el reporte por sede (que trae folio y
nombre de todos) podría abrir cualquier registro. Es una protección contra el
público en general, no contra alguien con acceso a ese reporte.

## Publicar por primera vez

1. Crea un repositorio en GitHub (puede ser privado si tu plan permite Pages
   privados; si es público no hay problema porque todo está cifrado).
2. Sube el contenido de esta carpeta.
3. En *Settings → Pages*: Source = *Deploy from a branch*, rama `main`, carpeta `/ (root)`.
4. En uno o dos minutos queda en `https://<usuario>.github.io/<repositorio>/`.

## Agregar una aplicación nueva o histórica

Requiere Python 3.10+ (una sola vez: `pip install -r herramientas/requirements.txt`).

```bash
python herramientas/agregar_resultados.py \
  --individuales Individuales_XXXX.zip \
  --global global_des_XXXX.pdf
git add datos && git commit -m "Resultados EGEL <fecha>" && git push
```

- `--individuales` acepta el ZIP de Ceneval, una carpeta o un PDF.
- `--global` es el *Reporte de resultados por sede*. Se usa para separar
  nombres de apellidos. Sin él, el script deduce el apellido y avisa.
- `--solo-revisar` muestra la tabla de lo que va a hacer sin escribir nada.
  Úsalo primero con generaciones históricas.
- `--apellidos casos.csv` (columnas `folio,apellido_paterno`) corrige casos
  especiales.
- `--examen "EGEL Odontología"` fija el nombre del examen si no se detecta.

Para formatos anteriores del EGEL: si el script no reconoce los puntajes, lo
avisa y el registro queda igual disponible para descargar el PDF.

Volver a correr el script con el mismo ZIP reemplaza esos registros, así que no
se duplica nada. **No subas los ZIP ni los PDFs originales**: `.gitignore` ya
excluye los `*.zip` y la carpeta `originales/`.

## Personalizar

En `index.html`, la constante `CONTACTO` (al inicio del `<script>`) define el
texto y el correo del pie de página.
