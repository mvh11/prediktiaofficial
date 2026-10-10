# M5.9B — Infraestructura local de predicción prospectiva

Solo local y simulada: ningún componente se conecta a producción ni a un scheduler, y no hay
predicciones reales. Diseño en M5.9A.

| Módulo | Qué hace |
| --- | --- |
| `canonical.py` | JSON canónico (claves ordenadas, UTC ISO-8601, sin NaN) y sha256 |
| `manifest.py` | Manifiesto sellado de B0/B1/B2 e inferencia en Python puro (solo `json.loads` + validación; sin pickle) |
| `freeze.py` | Crea manifiestos desde modelos ajustados de M5.8 (entorno de investigación) |
| `registry.py` | Registro JSONL append-only: `seq`, `prev_hash`, `record_hash`; detecta modificación, borrado, duplicado y reordenación; idempotente por `prediction_id` |
| `anchors.py` | Anclaje externo de la cabeza del registro y cobertura "anterior al kickoff" |
| `emitter.py` | Emisor con reloj inyectable: T = K − 1 h, H = emisión <= T, sin emisión tardía ni fallback |
| `evaluation.py` | Universo elegible, clases, denominadores, preregistro y evaluación confirmatoria única |

## Contrato temporal
- K y la identidad del partido: conocimiento estricto en H (instante de emisión). UNKNOWN/AMBIGUOUS
  → `no_emission`. T = K − 1 h; si la emisión (o su escritura) es posterior a T → `no_emission LATE`.
- Features: `team_recent_form_v1` con corte = horizonte = H (OPERATIONAL_STRICT).
- Identidad del objetivo no válida → `no_emission IDENTITY_INVALID`. Ventanas sin muestras o
  historia ambigua → `DEGRADED`: B0/B1 sí, B2 = null.
- El registro nunca contiene resultados; las etiquetas se unen solo en la evaluación.

## Anclaje externo (prueba de anterioridad)
Un commit local no prueba nada (su fecha la pone quien escribe). Antes de cada tanda de kickoffs se
publica `anchors.make_statement(registro)` = `{registry_seq, head_hash, statement_sha256}` ante una
autoridad cuyo instante no controla Prediktia:
- `rfc3161`: sello de tiempo de una TSA sobre `statement_sha256` (el token se guarda y se verifica
  con el certificado de la TSA);
- `github_server`: commit con la declaración empujado al remoto; vale la hora del servidor (evento
  de push o API), no la del commit;
- `project_thread`: la declaración publicada en el hilo del proyecto; vale la hora del mensaje.
Una predicción solo es evaluable si un anclaje verificado con `registry_seq >=` el suyo tiene
instante externo anterior al kickoff final. La verificación de cada autoridad se inyecta
(`external_check`); su implementación concreta es parte de M5.9C/M5.9D.

## Estado del registro y coberturas (ajuste de cierre)
- `registry_status` separa tres comprobaciones: `CHAIN_VALID` (cadena íntegra), `ANCHOR_VERIFIED`
  (al menos un anclaje externo verificado sobre esta cadena y ningún anclaje presentado que falle;
  uno que apunte a un seq inexistente delata una cadena truncada) y `EVALUATION_ELIGIBLE`
  (las dos anteriores). Una cadena íntegra sin anclaje no es evaluable. Sin `EVALUATION_ELIGIBLE`
  la evaluación confirmatoria no se ejecuta ni consume su única oportunidad.
- Además, cada predicción solo es evaluable si un anclaje verificado anterior a su kickoff final la
  cubre (si no, `UNANCHORED`).
- Cobertura **operativa**: denominador = todos los partidos elegibles para emisión, con o sin
  etiqueta (emitidas, informativas, degradadas, `NO_RECORD`, `NOT_EMITTED`).
- Cobertura **evaluable**: denominador = partidos elegibles con etiqueta verificable; numerador =
  `INFORMATIVE` (se cuentan aparte `NO_LABEL`, `DEGRADED`, `NO_RECORD`).
- El umbral confirmatorio `informative_coverage_min = 0,60` se mide hoy sobre la cobertura
  evaluable. Qué denominador rige el umbral (evaluable u operativo) se someterá a decisión
  gerencial antes del preregistro definitivo; los umbrales no han cambiado.
