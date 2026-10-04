# Reglas para agentes

Reglas que sigue cualquier agente (o persona) que trabaje en Prediktia. Las líneas de trabajo y su reparto están en [workstreams.md](workstreams.md).

Si una tarea concreta contradice una regla general de este documento, prevalece únicamente lo que el usuario autorice explícitamente para esa tarea. Los permisos no se deducen: lo que no se ha autorizado de forma explícita no está autorizado.

## Antes de empezar

- **Leer siempre** [workstreams.md](workstreams.md). Quien trabaja en Data Integrity / Sync lee también [data-integrity-status.md](data-integrity-status.md).
- **Nunca trabajar directamente en `main`.**
- **Comprobar el punto de partida:** rama actual, `git status` y último commit.
- **Working tree sucio:** si hay cambios que la tarea no autoriza, detenerse y reportarlos antes de tocar nada.

## Git

- **Sin autorización explícita del usuario, no se hace** merge, rebase, pull, cherry-pick, commit ni push.
- **No reescribir commits ya publicados ni hacer force-push** salvo autorización explícita.

## Alcance

- **No modificar archivos fuera del alcance** de la tarea.
- **Sin refactors cosméticos ni abstracciones hipotéticas:** solo lo que la tarea necesita.
- **Hallazgos fuera de alcance:** si se encuentra un problema fuera del alcance de la tarea, se documenta y se reporta, pero no se corrige automáticamente.
- **Ownership entre carriles:** encontrar un error durante una tarea no cambia automáticamente de quién es el problema.
  - Si el problema pertenece al otro carril, no se corrige automáticamente para desbloquear el trabajo.
  - Hay que detenerse y reportar:
    - archivo;
    - función o clase;
    - síntoma;
    - por qué bloquea la tarea actual;
    - cambio mínimo propuesto;
    - impacto del cambio propuesto;
    - si existe un workaround local que no modifique el otro carril.
  - Solo se cruza el límite entre carriles con autorización explícita del usuario.

## Responsabilidades de cada carril

- **`feature/data-integrity-sync` (Data Integrity / Sync):** integridad de datos e ingestión. Incluye sincronización, adapters, semántica de fixtures y resultados, idempotencia, retries y rate limits, backfills, consistencia entre proveedor y BD, y seguridad de los tests destructivos.
- **`feature/modular-data` (Modular):** desarrollo funcional por módulos. Incluye estadísticas, jugadores, player stats, injuries/suspensions, lineups, events y los siguientes módulos aprobados del roadmap.

## Arquitectura y datos

- **Flujo de datos:** Proveedor → Adapter → Modelo interno → Service → Repository → PostgreSQL. Cada capa se respeta; ninguna se salta.
- **Prediktia es dueña de sus datos:** el modelo interno y la BD no dependen del formato de ningún proveedor.
- **La semántica del proveedor se queda en el adapter:** la traducción de formatos y las particularidades de cada proveedor quedan encapsuladas en la capa de adapter/integración.
- **Evitar look-ahead bias y data leakage:** ningún cálculo usa información que no estaba disponible en el momento que se modela.

## Decisiones de Data Integrity

- **Pares atómicos:** cada marcador (local, visitante) se guarda o se descarta entero; nunca se mezcla un valor nuevo con uno viejo ni se guarda medio par.
- **NULL no destructivo:** una respuesta del proveedor con NULL no borra un marcador ya guardado; un par completo nuevo sí lo sustituye.
- **Fallback en FT:** si un partido FT no trae el marcador a 90', se usa `goals`, porque no hubo prórroga.
- **Sin fallback en AET/PEN:** ahí `goals` incluye la prórroga, así que nunca sustituye al marcador a 90'.
- **Errores de BD aislables:** `IntegrityError`, `DataError` y `OperationalError` pueden aislarse por competición, haciendo rollback de esa competición y permitiendo continuar la sync. Los errores de programación o de esquema no se ocultan ni se convierten en errores aislados: se propagan.
- **Tests destructivos fail-closed:** si el destino no está autorizado explícitamente, no se ejecutan. La autorización actual identifica exactamente host, puerto y base de datos mediante `TEST_DATABASE_ALLOW_DESTRUCTIVE=<host>:<puerto>/<bd>`.

## Migraciones

- **No reescribir** `backend/alembic/versions/0004_provider_mappings_fulltime.py`. Cualquier cambio de esquema va en una migración nueva.
- **Una migración Alembic nueva a la vez:** solo un carril crea una migración nueva en cada momento.

## Tests y base de datos

- **Nunca usar el Neon productivo para tests destructivos.**
- **Si no hay una BD segura**, los tests de BD se reportan como **NO EJECUTADOS**, no como pasados.
- **No inventar resultados:** solo se reporta lo que se ha ejecutado y comprobado.

## Al terminar una tarea

Reportar:

- archivos modificados y nuevos;
- tests ejecutados y su resultado (passed/skipped), indicando si los de BD se ejecutaron;
- migraciones creadas, si las hay;
- `git diff --check`;
- `git diff --stat`;
- `git status --short`;
- riesgos pendientes.
