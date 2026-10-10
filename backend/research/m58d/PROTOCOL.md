# M5.8D — Protocolo exploratorio (registrado antes de extraer datos)

Experimento EXPLORATORIO. Ningún resultado de aquí confirma señal predictiva. El test de M5.8B
(kickoff >= 2026-01-01), sus criterios y su resultado (INCONCLUSIVE) no se tocan: ninguna fila con
kickoff >= 2026-01-01 entra en el dataset, el diagnóstico ni la selección de M5.8D.

## Datos
- Snapshot nuevo de solo lectura (procedimiento auditado de M5.7C/M5.8B), restaurado en PostgreSQL
  18 local con `0008` aplicada solo en local. H = bootstrap_at local (fijo). `HISTORICAL_BACKTEST`.
- Universo: partidos de temporadas con alguna observación de estadísticas, kickoff < 2026-01-01.
- Features: `team_recent_form_v1` (sin cambios) de local y visitante, T = kickoff − 1 h.

## Etiquetas (corrección de M5.8C)
- FT, AET y PEN: 1X2 según el marcador `fulltime` (90') del propio partido. No se supone empate
  por estado AET/PEN (eliminatorias a doble partido). Nunca `goals` (incluye la prórroga).
- Sin etiqueta: estado no FT/AET/PEN, `fulltime` incompleto, evidencia no KNOWN en H.
- Control de calidad (no excluye): con `extratime` presente, `goals = fulltime + extratime`.

## Cobertura por causa (cada partido de cada ventana)
1. `NEVER_INGESTED`: el partido no tiene ninguna observación de estadísticas.
2. `NOT_AVAILABLE_AT_T`: hay observación, pero `available_at > T` (o `observed_at > H`).
3. `EMPTY`: la observación disponible no trae estadísticas.
4. `QUALITY_OR_IDENTITY`: BLOCKED / RECONSTRUCTION_MISMATCH / IDENTITY_UNVERIFIED.
Además: `USED`, `EXCLUDED_EXTRA_TIME`, `TEAM_STATISTICS_MISSING`. Por competición, temporada,
fuente de la observación y feature.

## Validación temporal: origen móvil por trimestres (solo < 2026-01-01)
- Orígenes O ∈ {2025-01-01, 2025-04-01, 2025-07-01, 2025-10-01}; evaluación = [O, O + 3 meses).
- Ajuste: kickoff < O − 7 días (embargo). Dentro del ajuste, conjunto de calibración/selección =
  los 90 días anteriores a O − 7 días; el modelo que lo predice se entrena con kickoff <
  (inicio de calibración − 7 días). Selección (C, λ, alpha) y calibradores se ajustan SOLO con
  esas predicciones; nunca con las de evaluación.

## Modelos (todos los intentos se registran, también los negativos)
- B0 frecuencias globales; B1 por competición (alpha elegido en calibración).
- B2(C) logística multinomial L2 para C ∈ {0,001 … 1}: efecto de la regularización.
- B2*: C elegido en calibración, reentrenado con todo el ajuste.
- B2cal: B2* del conjunto previo + calibración por clase (logística multinomial sobre log p)
  ajustada en calibración, aplicada en evaluación; se compara con ese mismo modelo sin calibrar.
- ORD: logit ordinal (H < D < A) L2, λ elegido en calibración.
- Sin Elo ni features de resultados (trigger G-1 de DI), sin GBM.

## Métricas
Log loss (principal), Brier, RPS; calibración por clase (pendiente e intercepto con IC 95 % por
bootstrap de semanas, ECE); distribución de p(D); diferencias pareadas frente a B0/B1 con IC;
cobertura; sensibilidad por pliegue, competición y temporada.
