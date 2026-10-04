# Líneas de trabajo en paralelo

Hay dos ramas activas que avanzan a la vez. Cada una toca solo su área para que el merge a `main` sea limpio.

| Rama | Carril | Área |
| --- | --- | --- |
| `feature/data-integrity-sync` | Data Integrity / Sync | Integridad de datos e ingestión: sincronización, adapters, semántica de fixtures/resultados, idempotencia, retries/rate limits, backfills, consistencia proveedor/DB y seguridad de tests destructivos. |
| `feature/modular-data` | Modular | Desarrollo funcional por módulos: estadísticas, jugadores, player stats, injuries/suspensions, lineups, events y siguientes módulos aprobados del roadmap. |

## Reglas comunes

- **Base común original de ambos carriles:** `298b2f3` ("mudar a neon 004"). A partir de ese punto las ramas evolucionan independientemente hasta su integración coordinada en `main`.
- **Migración 0004:** `alembic/versions/0004_provider_mappings_fulltime.py` ya está en `main`. No se rehace ni se modifica. Cualquier cambio de esquema va en una migración nueva.
- **Cada rama toca solo su área.**
- **Archivos del otro carril:** si una tarea requiere modificar un archivo trabajado por el otro carril, no se hace silenciosamente. Se identifica el archivo, se explica el cambio necesario y se coordina primero.
- **Una migración Alembic nueva a la vez:** solo un carril puede crear una migración nueva en cada momento.
- **Nada de pull, merge ni rebase entre ramas** sin acordarlo. Cada rama se integra en `main` por separado.

## Estado de cada carril

Cada carril mantiene su propio documento de estado:

- **Data Integrity / Sync:** [data-integrity-status.md](data-integrity-status.md).
- **Modular:** [modular-data-status.md](modular-data-status.md).

Se actualizan al cerrar bloques significativos o antes de un checkpoint, no en cada cambio menor.
