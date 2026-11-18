# Migración del repo: `LinkedOut` limpio y `BT4_old` como archivo

Pedido del creador (12/11). Estrategia: el repo nuevo arranca con **un solo commit** cuyo árbol es el `develop` ya saneado; la
historia queda consultable en `BT4_old`. **Etapa 2 solo con el OK del coordinador, después del corte de producción.**

## Estado (14/11)
- Hecho: el saneado de datos personales está en `develop` (`a2dbb79`, CI en verde).
  - plantilla del CV derivada sin datos (`tools/derivar_plantilla.py`), con el mismo formato medido con `tools/comparar_docx.py`;
  - tests y fixtures con un candidato inventado completo.
- Excepciones aceptadas:
  - listas de partidos del GBA (perfil de ejemplo, tests, prompt de `core/triage.py`);
  - `tools/auditar_datos.py` y sus tests, que nombran patrones a propósito.
- Falta: la etapa 2 (GitHub) y repuntar los clones.

## Ajustes (17/11), mandan sobre el resto del documento
- **El corte de producción ya está hecho** (17/11 03:24): producción corre el clon `~/LinkedOut/produccion`.
- **«CI en verde» se reemplaza por la suite completa local**, porque el CI de GitHub no tiene minutos. Vale el informe en verde de la compuerta nocturna, o la suite en el clon nuevo con la autorización del creador.
- **El clon de producción pasa al repo nuevo sin reiniciar:**
  - `fetch` del repo nuevo y `reset --hard` a la semilla, que tiene el mismo árbol: no cambia ningún archivo;
  - después, `set-url` y un hook `pre-push` que rechaza todo;
  - los tags `deploy-*` y las versiones viejas quedan en ese clon para el rollback.
- **Renombrar el repo en GitHub y crear el nuevo es externo:** solo con el OK del creador.

## Invariantes
- Nada se borra: ni `BT4_old`, ni el checkout de desarrollo, ni los worktrees, ni la producción vieja.
- Ningún force push sobre el repo viejo.
- La base real y el `.env` no se tocan.
- Producción sigue andando. Si hay conflicto, primero la funcionalidad del portal y después la limpieza.

## Precondiciones
1. Backup 7z completo y verificado (`tools/backup_local.ps1`, `7z t` OK).
2. Ninguna sesión con trabajo sin mergear a `develop`. Lo que no pasa queda solo en `BT4_old`.
3. Corte de producción hecho, y el CI de `develop` en verde sobre la punta que se va a migrar.

## Riesgo principal y guardia
Al renombrar, GitHub redirige `nullpointlol/LinkedOut` a `BT4_old`. Cuando se crea el `LinkedOut` nuevo, la URL vieja
pasa a apuntar al nuevo. **Un clon viejo que haga push a «LinkedOut» subiría la historia sucia al repo nuevo.**

Hay que tapar todo lo que tenga la URL, **antes** de crear el repo nuevo:
- **El `.git` de desarrollo:** checkout principal y todos sus worktrees, que comparten un solo `origin`.
  - `git remote set-url origin https://github.com/nullpointlol/BT4_old.git`.
  - Hook `pre-push` en ese `.git` que rechace toda URL con `linkedout` y toda rama `*historico*`.
  - Prueba: un push a un bare local llamado `LinkedOut.git` tiene que dar BLOQUEADO.
- **El clon de producción** (después del corte, `~/LinkedOut/produccion`, con su propio `origin`): `set-url` a `BT4_old` y
  el mismo hook. `tools/deploy.ps1` saca el repo de `git remote get-url origin`, así que no tiene la URL escrita.
- **Cualquier otro clon:** buscar `.git` con `remote` que contenga `LinkedOut` y repuntarlo igual.

## Secuencia (etapa 2)
Variables: `$V` = `.git` de desarrollo; `$M` = carpeta de trabajo fuera de cualquier repo; `$N` = clon nuevo.
1. **Foto previa.**
   - `git -C $V ls-remote origin > $M/refs-antes.txt`;
   - `git -C $V rev-parse develop^{tree} > $M/arbol.txt`.
2. **Semilla** (un commit, sin historia):
   ```bash
   S=$(git -C $V commit-tree "develop^{tree}" -m "LinkedOut: árbol saneado. Historia anterior en nullpointlol/BT4_old (develop <sha>)")
   git init --bare $M/semilla.git
   git -C $V push $M/semilla.git "$S:refs/heads/develop" "$S:refs/heads/main"
   ```
   - **Control:** `git -C $M/semilla.git rev-list --all --count` = 1.
   - Auditoría de datos personales sobre la semilla: 0 críticos y 0 altos. **Si falla, se para acá: GitHub no se tocó.**
3. **Guardias:** el hook y el bare de prueba de la sección anterior, en el `.git` de desarrollo y en producción.
4. **Renombrar:**
   - `gh repo rename BT4_old -R nullpointlol/LinkedOut --yes`;
   - `set-url` a `BT4_old` en todos los clones.
   - **Control:**
     - `ls-remote` idéntico a `refs-antes.txt`;
     - `gh api repos/nullpointlol/LinkedOut --jq .full_name` → `nullpointlol/BT4_old` (la redirección existe).
5. **Opcional, con OK:** `git -C $V push origin "refs/tags/deploy-*"` sube los tags a `BT4_old`, y
   `gh workflow disable CI -R nullpointlol/BT4_old`.
6. **Crear:** `gh repo create nullpointlol/LinkedOut --private`.
   - **Control:** `.private` = true y `.size` = 0;
   - todos los clones siguen en `BT4_old`.
7. **Subir solo la semilla**, con refs explícitos y nunca `--all` ni `--mirror`:
   - `git -C $M/semilla.git push https://github.com/nullpointlol/LinkedOut.git refs/heads/develop refs/heads/main`;
   - `gh repo edit nullpointlol/LinkedOut --default-branch main`.
   - **Control:**
     - ramas = {develop, main};
     - 1 commit;
     - el CI corre.
8. **Clon nuevo:**
   - `git clone https://github.com/nullpointlol/LinkedOut.git $N`;
   - `rev-parse develop^{tree}` = `arbol.txt`, es decir, el mismo código byte a byte;
   - CI en verde sobre la semilla.
9. **Entorno de las sesiones:**
   - copiar `.claude/settings.local.json` y la memoria del proyecto a la clave de la carpeta nueva;
   - las sesiones nuevas se abren en `$N`.

## Producción y rollback
- **Tags `deploy-*` y rollback a versiones anteriores a la migración:** quedan en el `.git` viejo o en el clon de
  producción, que siguen apuntando a `BT4_old`. Los commits viejos no existen en el repo nuevo.
- **Primera promoción desde el repo nuevo** (solo con el OK del creador):
  1. un clon de producción nuevo, desde `LinkedOut`, en el commit semilla, que tiene el mismo árbol que el `develop` migrado;
  2. backup;
  3. cambiar la carpeta de producción en la config;
  4. reiniciar el servicio y chequear la salud.
- **Vuelta atrás:** volver a la carpeta anterior en la config y reiniciar.
- **Línea en `despliegues.log`:** `MIGRACIÓN ... repo nuevo, commit <S>; árbol = develop <sha> de BT4_old`.

## Después de migrar
- **`AGENTS.md`:** «`nullpointlol/BT4_old` es solo lectura: nunca push, merge, fetch ni cherry-pick hacia LinkedOut».
- **Trabajo de una rama vieja:** se porta con parches, sin traer historia:

  `git -C <viejo> format-patch develop..<rama> --stdout | git -C $N am -3`
- **Verificación final:**
  - CI en verde en un clon nuevo;
  - promoción desde el repo nuevo;
  - portal 1111 sano;
  - auditoría repetida sobre el repo nuevo con 0 críticos y 0 altos.
