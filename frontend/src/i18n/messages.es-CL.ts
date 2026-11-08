/**
 * Catálogo de textos de la interfaz en español de Chile (idioma principal).
 *
 * Es la fuente de verdad del tipo `Messages`: otro idioma se agrega creando un
 * catálogo con la misma forma (`const en: Messages = { ... }`) y entregándolo a
 * <I18nProvider>. La lógica de dominio nunca devuelve textos: devuelve códigos
 * (fase del partido, categoría de error, tipo de marcador) que se traducen aquí.
 *
 * Reglas de redacción: tuteo, terminología futbolística chilena (alargue,
 * penales, postergado), nunca insinuar que un dato sincronizado es en vivo, y
 * nunca mostrar probabilidades, cuotas, valor esperado ni recomendaciones.
 */
export const esCL = {
  site: {
    name: 'PREDIKTIA',
    titleSeparator: ' · ',
  },

  layout: {
    skipLink: 'Saltar al contenido principal',
    homeLink: 'PREDIKTIA: ir al inicio',
    stage: 'Versión de investigación',
    primaryNav: 'Principal',
    mobileNav: 'Menú móvil',
    menu: 'Menú',
    closeMenu: 'Cerrar menú',
    footerNav: 'Enlaces del pie de página',
    disclaimer:
      'Versión de investigación. PREDIKTIA no publica pronósticos certificados, cuotas, valores esperados ni recomendaciones de apuestas. Nada en este sitio constituye asesoría de apuestas ni financiera.',
    dataNote: 'Datos de fútbol almacenados por PREDIKTIA y mostrados según su última sincronización. No es un servicio en vivo.',
    loadingPage: 'Cargando página…',
    breadcrumbs: 'Ruta de navegación',
  },

  nav: {
    home: 'Inicio',
    competitions: 'Competiciones',
    fixtures: 'Partidos',
    research: 'Investigación',
    methodology: 'Metodología',
  },

  theme: {
    legend: 'Apariencia',
    system: 'Según el sistema',
    light: 'Clara',
    dark: 'Oscura',
  },

  network: {
    offlineTitle: 'Sin conexión a internet.',
    offlineBody: 'No se pueden cargar ni actualizar datos hasta que vuelva la conexión.',
  },

  service: {
    label: 'Servicio de datos:',
    checking: 'Comprobando…',
    connected: 'Conectado',
    unavailable: 'No disponible',
    notConfigured: 'No configurado',
    offline: 'Sin conexión',
  },

  states: {
    retry: 'Reintentar',
    slow: 'Está tardando más de lo habitual. El servicio de datos puede estar lento; puedes seguir esperando.',
  },

  /** Frases nominales usadas dentro de los mensajes de error («No fue posible cargar …»). */
  subjects: {
    competitions: 'el listado de competiciones',
    competition: 'esta competición',
    teams: 'los equipos de esta temporada',
    fixtures: 'la lista de partidos',
    fixture: 'este partido',
    upcoming: 'los próximos partidos registrados',
  },

  errors: {
    notConfiguredTitle: 'Fuente de datos no configurada',
    notConfigured: (subject: string) =>
      `Esta instalación no tiene configurado el servicio de datos de PREDIKTIA, por lo que no es posible cargar ${subject}. No se muestra nada en su lugar.`,
    offlineTitle: 'Sin conexión a internet',
    offline: (subject: string) =>
      `Tu dispositivo no tiene conexión, así que no fue posible cargar ${subject}. Vuelve a intentarlo cuando se restablezca.`,
    unavailableTitle: 'Servicio de datos no disponible',
    unavailable: (subject: string) =>
      `No fue posible contactar al servicio de datos de PREDIKTIA para cargar ${subject}. Puede ser una interrupción temporal.`,
    unavailableStatus: (subject: string, status: number) =>
      `El servicio de datos no está respondiendo correctamente (HTTP ${status}), así que no fue posible cargar ${subject}.`,
    timeoutTitle: 'La solicitud tardó demasiado',
    timeout: (subject: string, seconds: number) =>
      `El servicio de datos no respondió en ${seconds} segundos, así que no fue posible cargar ${subject}.`,
    invalidTitle: 'Respuesta con formato inesperado',
    invalid: (subject: string) =>
      `El servicio de datos respondió con un formato que no coincide con el contrato verificado al cargar ${subject}. Para no mostrar información incorrecta, se descartó la respuesta completa.`,
    invalidNotJson: 'La respuesta no está en formato JSON.',
    invalidMalformedJson: 'La respuesta JSON está incompleta o mal formada.',
    wholeRecord: 'registro completo',
    invalidField: (field: string) => `El campo «${field}» no cumple el formato esperado.`,
    invalidRow: (row: number, field: string) =>
      `El registro n.º ${row} de la respuesta no cumple el formato esperado (campo «${field}»).`,
    invalidRowWithId: (row: number, id: string, field: string) =>
      `El registro n.º ${row} de la respuesta (identificador ${id}) no cumple el formato esperado (campo «${field}»).`,
    invalidOneRow:
      'Un solo registro inconsistente invalida toda la página: PREDIKTIA no elimina ni corrige datos en silencio. Si el problema persiste, el dato debe corregirse en el origen.',
    unsafeIntegerTitle: 'Número no representable con exactitud',
    unsafeInteger: (literal: string) =>
      `La respuesta contiene el número ${literal}, que supera el máximo que el navegador puede representar sin perder precisión. Para no mostrar un dato alterado (un identificador, una temporada o un marcador), se descartó la respuesta completa.`,
    invalidIdentifierTitle: 'Identificador no válido en la respuesta',
    invalidIdentifier: (literal: string, field: string) =>
      `El campo «${field}» de la respuesta trae «${literal}», que no es un identificador entero canónico dentro del rango admitido (1 a 2.147.483.647 para identificadores internos). Interpretarlo podría redondearlo a otro número, así que se descartó la respuesta completa.`,
    invalidIntegerTitle: 'Número entero no válido en la respuesta',
    invalidInteger: (literal: string, field: string) =>
      `El campo «${field}» de la respuesta trae «${literal}», que no es un número entero exacto (sin decimales ni exponente) dentro del rango admitido. Interpretarlo podría redondearlo a otro valor, por ejemplo otra temporada u otro marcador, así que se descartó la respuesta completa.`,
    invalidTokenRow: (row: number) => `El valor está en el registro n.º ${row} de la respuesta.`,
    identityMismatchTitle: 'La respuesta no corresponde al partido solicitado',
    identityMismatch: (expected: string, received: string) =>
      `Se pidió el partido ${expected}, pero el servicio de datos respondió con el partido ${received}. Para no mostrar un partido distinto, no se muestra nada.`,
    notFoundTitle: 'No encontrado',
    notFound: (subject: string) => `No fue posible encontrar ${subject} en el almacén de datos de PREDIKTIA.`,
    rejectedTitle: 'Solicitud rechazada',
    serverErrorTitle: 'Error del servicio de datos',
    rejected: (subject: string, status: number) => `El servicio de datos no pudo entregar ${subject} (HTTP ${status}).`,
    unexpectedTitle: 'Algo salió mal',
    unexpected: (subject: string) => `Un error inesperado impidió cargar ${subject}.`,
  },

  ids: {
    invalidTitle: 'Identificador no válido',
    invalid: (raw: string) =>
      `«${raw}» no es un identificador válido. Los identificadores son números enteros positivos, sin ceros a la izquierda ni otros caracteres.`,
    unsupportedTitle: 'Identificador fuera del rango admitido',
    unsupported: (raw: string, max: string) =>
      `El identificador ${raw} supera el máximo admitido por el servicio de datos (${max}). No se realizó ninguna consulta y el número no se redondeó ni se recortó.`,
    ignoredParams: 'Se ignoraron filtros no válidos de la dirección:',
    ignoredParam: (name: string, raw: string, reason: 'invalid' | 'unsupported') =>
      `${name} = «${raw}» (${reason === 'unsupported' ? 'fuera del rango admitido' : 'valor no válido'})`,
  },

  home: {
    documentTitle: 'Inicio',
    kicker: 'Investigación cuantitativa de fútbol',
    title: 'Probabilidades de fútbol basadas en evidencia, o ninguna.',
    lead: 'PREDIKTIA está construyendo una plataforma de investigación para el fútbol: datos en su punto en el tiempo, modelos probabilísticos transparentes y controles rigurosos de calibración. Mientras no exista esa evidencia, no publica pronósticos.',
    ctaCompetitions: 'Explorar competiciones',
    ctaFixtures: 'Ver partidos',
    ctaMethodology: 'Leer la metodología',
    statusLine: 'Versión de investigación · Sin pronósticos certificados · Sin recomendaciones de apuestas',
    journeyTitle: 'Cómo recorrer PREDIKTIA',
    journeyLead: 'Cada paso usa solo datos almacenados y verificados. Donde falta evidencia, la interfaz lo dice.',
    journey: [
      {
        title: 'Competiciones',
        body: 'Revisa las ligas y copas registradas, su temporada actual y los equipos de cada temporada.',
        cta: 'Ver competiciones',
      },
      {
        title: 'Partidos',
        body: 'Busca partidos por competición, temporada, equipo, estado o rango de fechas. Los filtros quedan en la dirección para compartirlos.',
        cta: 'Buscar partidos',
      },
      {
        title: 'Detalle del partido',
        body: 'Marcadores tal como se almacenaron (90 minutos, alargue y penales por separado), estadio, árbitro y estado registrado.',
        cta: 'Elegir un partido',
      },
      {
        title: 'Contexto de investigación',
        body: 'Qué estudia la investigación, qué falta para poder publicar un pronóstico y por qué hoy cada partido resulta en ABSTAIN.',
        cta: 'Ver la investigación',
      },
    ],
    principlesTitle: 'Cómo trabaja PREDIKTIA',
    principlesLead:
      'Cuatro principios guían toda la plataforma, desde la ingesta de datos hasta lo que esta interfaz tiene permitido mostrar.',
    principles: [
      {
        title: 'Datos con procedencia',
        body: 'Partidos y resultados llegan desde proveedores externos a través de adaptadores aislados, y cada observación queda registrada para poder auditar su origen y su momento.',
      },
      {
        title: 'Investigación en su punto en el tiempo',
        body: 'Los modelos solo pueden usar información disponible antes del inicio del partido. Las variables se reconstruyen «a la fecha» para descartar el sesgo de anticipación.',
      },
      {
        title: 'Calibración antes que afirmaciones',
        body: 'Una probabilidad solo sirve si está calibrada. Los modelos se evalúan fuera de muestra, con reglas de puntuación propias y diagnósticos de calibración, antes de considerar cualquier publicación.',
      },
      {
        title: 'ABSTAIN por defecto',
        body: 'Cuando la evidencia falta o es insuficiente, la respuesta correcta es no responder. PREDIKTIA se abstiene en vez de adivinar; hoy eso aplica a todos los partidos.',
      },
    ],
    statusTitle: 'Dónde está la plataforma hoy',
    statusLead: 'Una descripción directa de lo que existe y lo que no. Esta lista se mantiene junto al código, no desde marketing.',
  },

  capabilities: {
    catalog: {
      name: 'Catálogo de competiciones y partidos',
      status: 'Solo lectura',
      description:
        'Competiciones, equipos y partidos almacenados por PREDIKTIA, mostrados tal como quedaron en la última sincronización cuando el servicio de datos está conectado. No es un servicio en vivo.',
    },
    research: {
      name: 'Modelos de investigación',
      status: 'Solo investigación',
      description:
        'Modelos de distribución de goles, referencias base y diagnósticos de calibración evaluados con datos históricos. Sus resultados no se publican como pronósticos.',
    },
    predictions: {
      name: 'Pronósticos certificados',
      status: 'No disponibles',
      description: 'Ningún pronóstico ha sido certificado ni autorizado para su publicación. Hoy todos los partidos resultan en ABSTAIN.',
    },
    odds: {
      name: 'Cuotas y datos de mercado',
      status: 'Sin fuente verificada',
      description:
        'No existe una fuente verificada y autenticada de cuotas de casas de apuestas. No se muestran cuotas ni cifras de valor esperado.',
    },
    recommendations: {
      name: 'Recomendaciones de apuestas',
      status: 'No se ofrecen',
      description: 'PREDIKTIA no recomienda apuestas ni montos, y no ofrece suscripciones ni datos pagados.',
    },
  },

  productCodes: {
    title: 'Estado del producto',
    ABSTAIN: 'Ante evidencia insuficiente, la salida del modelo es abstenerse: no se muestra ninguna probabilidad ni recomendación.',
    NOT_CERTIFIED: 'Ningún modelo ni resultado de investigación está certificado para su publicación.',
    WAIT_REAL_SOURCE: 'Se espera una fuente real, verificada y con sello de tiempo para cuotas y emisión de pronósticos.',
    RESEARCH_ONLY: 'Los modelos existen solo como investigación sobre datos históricos.',
  },

  evidence: {
    statusNotEstablished: 'No establecido',
    statusNotGranted: 'No otorgada',
    items: {
      pit: {
        requirement: 'Certificación estricta en el punto en el tiempo de las variables del modelo',
        why: 'Demuestra que cada variable se conocía antes del inicio del partido y descarta el sesgo de anticipación.',
      },
      issuance: {
        requirement: 'Hora de emisión autenticada de forma independiente',
        why: 'Un pronóstico solo cuenta si se puede demostrar que existía antes de que empezara el partido.',
      },
      emitter: {
        requirement: 'Emisor de modelos prospectivo y verificado',
        why: 'Los pronósticos deben salir de un modelo validado y versionado que corre hacia adelante en el tiempo, no de una prueba retrospectiva.',
      },
      odds: {
        requirement: 'Comprobantes auténticos de cuotas del proveedor y correspondencia certificada de mercados',
        why: 'Cualquier comparación con el mercado necesita cuotas genuinas, con sello de tiempo y correctamente asociadas.',
      },
      ev: {
        requirement: 'Metodología certificada de valor esperado',
        why: 'El valor esperado no tiene sentido sin probabilidades certificadas y cuotas auténticas.',
      },
      authorization: {
        requirement: 'Autorización para publicar pronósticos',
        why: 'Publicar es una decisión aparte y explícita, que solo se toma cuando toda la evidencia anterior existe.',
      },
    },
  },

  competitions: {
    documentTitle: 'Competiciones',
    eyebrow: 'Catálogo',
    title: 'Competiciones',
    lead: 'Ligas y copas registradas en PREDIKTIA, con su temporada actual según la última sincronización.',
    loading: 'Cargando competiciones…',
    searchLabel: 'Buscar competición',
    searchHint: 'Filtra por nombre o país. La búsqueda se hace sobre el listado completo.',
    searchSummary: (shown: number, total: number) =>
      shown === total ? `${total} ${total === 1 ? 'competición' : 'competiciones'}` : `${shown} de ${total} competiciones`,
    noMatchesTitle: 'Ninguna competición coincide con la búsqueda',
    noMatches: (query: string) => `No hay competiciones cuyo nombre o país contenga «${query}».`,
    clearSearch: 'Limpiar búsqueda',
    emptyTitle: 'Aún no hay competiciones registradas',
    empty: 'El almacén de datos de PREDIKTIA no contiene competiciones en este momento, así que no hay equipos ni partidos para explorar.',
    noCountry: 'Sin país registrado',
    typeLeague: 'Liga',
    typeCup: 'Copa',
    currentSeason: (season: number) => `Temporada actual: ${season}`,
    noCurrentSeason: 'Sin temporada actual registrada',
    viewFixtures: 'Ver partidos',
    listLabel: (country: string) => `Competiciones de ${country}`,
  },

  competition: {
    eyebrow: 'Competición',
    loadingTitle: 'Competición',
    loading: 'Cargando competición…',
    notFoundTitle: 'Competición no encontrada',
    notFound: 'No existe una competición con este identificador en el catálogo de PREDIKTIA.',
    backToList: 'Ver todas las competiciones',
    facts: 'Datos de la competición',
    country: 'País',
    type: 'Tipo',
    currentSeason: 'Temporada actual',
    noCurrentSeason: 'No registrada',
    notRecorded: 'No registrado',
    seasonPanelTitle: 'Temporada consultada',
    seasonFormLabel: 'Temporada',
    seasonFormHint: (current: number | null) =>
      current === null
        ? 'El servicio de datos no informa una temporada actual para esta competición. Indica el año de inicio de una temporada.'
        : `El servicio de datos solo informa la temporada actual (${current}); puedes consultar otro año de inicio.`,
    seasonSubmit: 'Ver temporada',
    seasonInvalid: 'Indica un año de temporada entre 1900 y 2100.',
    viewFixtures: (season: number) => `Ver partidos de la temporada ${season}`,
    viewAllFixtures: 'Ver partidos de todas las temporadas',
    teamsTitle: (season: number) => `Equipos de la temporada ${season}`,
    teamsTitleCurrent: 'Equipos de la temporada actual',
    teamsLoading: 'Cargando equipos…',
    teamsSeasonMissingTitle: 'Temporada no registrada',
    teamsSeasonMissing: (season: number | null) =>
      season === null
        ? 'Esta competición no tiene una temporada actual registrada, así que no hay equipos que mostrar. Prueba con un año de temporada concreto.'
        : `No hay registro de la temporada ${season} para esta competición.`,
    teamsEmptyTitle: 'Sin equipos registrados',
    teamsEmpty: 'No hay equipos registrados para esta temporada.',
    teamSearchLabel: 'Buscar equipo',
    teamSearchSummary: (shown: number, total: number) =>
      shown === total ? `${total} ${total === 1 ? 'equipo' : 'equipos'}` : `${shown} de ${total} equipos`,
    teamNoMatches: (query: string) => `Ningún equipo contiene «${query}».`,
    teamListLabel: 'Equipos',
    teamFounded: (year: number) => `Fundado en ${year}`,
    teamNational: 'Selección nacional',
    teamFixtures: 'Partidos',
    teamFixturesLabel: (team: string) => `Partidos de ${team}`,
    upcomingTitle: 'Próximos partidos registrados',
    upcomingLead:
      'Los primeros partidos no finalizados desde hoy (día UTC), según la última sincronización. Pueden incluir partidos postergados o cancelados.',
    upcomingLoading: 'Cargando próximos partidos…',
    upcomingEmptyTitle: 'Sin próximos partidos registrados',
    upcomingEmpty: 'No hay partidos no finalizados desde hoy para esta temporada en el almacén de datos.',
    upcomingAll: 'Ver todos los partidos desde hoy',
  },

  fixtures: {
    documentTitle: 'Partidos',
    title: 'Partidos',
    lead: 'Busca partidos almacenados por PREDIKTIA. Estados y marcadores corresponden a la última sincronización.',
    noticeTitle: 'Qué estás viendo',
    notice:
      'Datos almacenados, no en vivo: estados y marcadores son los de la última sincronización, cuya hora la API no informa. Ningún partido tiene pronósticos, cuotas ni recomendaciones.',
    filtersTitle: 'Filtros',
    filtersLabel: 'Filtros de partidos',
    showFilters: 'Mostrar filtros',
    hideFilters: 'Ocultar filtros',
    competition: 'Competición',
    competitionAll: 'Todas las competiciones',
    competitionHint: 'Se aplica al seleccionarla y carga sus equipos.',
    competitionsLoading: 'Cargando competiciones…',
    competitionsUnavailable: 'No fue posible cargar el listado de competiciones.',
    season: 'Temporada',
    seasonHint: (current: number | null) =>
      current === null ? 'Año de inicio. Sin valor: todas las temporadas.' : `Año de inicio. Por defecto, la temporada actual (${current}).`,
    seasonHintNoCompetition: 'Año de inicio. Sin valor: todas las temporadas.',
    seasonInvalid: 'Indica un año entre 1900 y 2100.',
    team: 'Equipo',
    teamAll: 'Todos los equipos',
    teamUnknown: (id: number) => `Equipo n.º ${id}`,
    teamNeedsCompetition: 'Elige una competición para filtrar por equipo.',
    teamsLoading: 'Cargando equipos…',
    teamsSeasonMissing: 'No hay lista de equipos registrada para esta temporada.',
    teamsUnavailable: 'La lista de equipos no está disponible en este momento.',
    teamsEmpty: 'No hay equipos registrados para esta temporada.',
    status: 'Estado',
    statusAll: 'Todos los partidos',
    statusPending: 'No finalizados (incluye postergados, cancelados y adjudicados)',
    statusFinished: 'Finalizados (tiempo reglamentario, alargue o penales)',
    from: 'Desde (día UTC)',
    to: 'Hasta (día UTC)',
    dateHint: 'El servicio filtra por día UTC: un partido nocturno en Chile puede quedar en el día UTC siguiente.',
    dateRangeError: 'La fecha «hasta» debe ser igual o posterior a la fecha «desde».',
    dateOutOfRange: 'Indica un día entre el 1 de enero de 1900 y el 31 de diciembre de 2100.',
    apply: 'Aplicar filtros',
    reset: 'Restablecer',
    quickRanges: 'Rangos rápidos (días UTC)',
    quickToday: 'Hoy',
    quickNext7: 'Próximos 7 días',
    quickLast7: 'Últimos 7 días',
    activeFilters: 'Filtros activos',
    removeFilter: (label: string) => `Quitar filtro: ${label}`,
    chipCompetition: (name: string) => `Competición: ${name}`,
    chipSeason: (season: number) => `Temporada ${season}`,
    chipTeam: (name: string) => `Equipo: ${name}`,
    chipFrom: (day: string) => `Desde ${day}`,
    chipTo: (day: string) => `Hasta ${day}`,
    chipStatusPending: 'No finalizados',
    chipStatusFinished: 'Finalizados',
    resultsTitle: 'Resultados',
    resultsFor: (competition: string) => `Resultados: ${competition}`,
    allSeasonsNote: 'Esta competición no tiene temporada actual registrada; se muestran todas las temporadas.',
    loading: 'Cargando partidos…',
    invalidRangeTitle: 'Rango de fechas no válido',
    invalidRange: 'La fecha «hasta» es anterior a la fecha «desde». Corrige las fechas y vuelve a aplicar los filtros.',
    competitionNotFoundTitle: 'Competición no encontrada',
    competitionNotFound: 'La competición indicada en la dirección no está en el catálogo de PREDIKTIA.',
    clearCompetition: 'Quitar filtro de competición',
    emptyTitle: 'No se encontraron partidos',
    empty: 'Ningún partido del almacén de datos coincide con estos filtros. Prueba con otra temporada, equipo, estado o rango de fechas.',
    noMoreTitle: 'No hay partidos en esta página',
    noMore:
      'La página solicitada no tiene partidos para estos filtros. El servicio no informa el total de resultados, así que no es posible saber de antemano cuántas páginas existen.',
    previousPageFallback: 'Volver a la página anterior',
    firstPage: 'Ir a la primera página',
    range: (first: number, last: number) => `Partidos ${first} a ${last}, ordenados por fecha y hora de inicio.`,
    lastPageNote: 'Fin de los resultados para estos filtros.',
    pagination: 'Paginación',
    previous: 'Página anterior',
    next: 'Página siguiente',
    page: (page: number) => `Página ${page}`,
    listLabel: 'Partidos',
    dayLabel: (day: string) => `Partidos del ${day}`,
    timeZoneNote: 'Horas en hora de Chile continental (America/Santiago).',
    versus: 'vs',
    syncedInPlay: 'al sincronizar',
  },

  fixture: {
    eyebrow: 'Partido',
    loadingTitle: 'Partido',
    loading: 'Cargando partido…',
    notFoundTitle: 'Partido no encontrado',
    notFound: 'No existe un partido con este identificador en el almacén de datos de PREDIKTIA.',
    browse: 'Ver partidos',
    back: 'Volver a los partidos',
    title: (home: string, away: string) => `${home} vs ${away}`,
    kickoffLead: 'Inicio:',
    scoreTitle: 'Marcador',
    scoreCaption: (title: string) => `Marcadores registrados de ${title}`,
    period: 'Periodo',
    finalNote:
      'El resultado final incluye el alargue cuando se jugó; la definición a penales se muestra por separado. Los marcadores se muestran tal como se almacenaron en la última sincronización.',
    notFinalNote:
      'Este partido no había terminado en la última sincronización, así que el marcador mostrado no es un resultado final. Los marcadores se muestran tal como se almacenaron.',
    noScore: 'No hay marcador registrado para este partido.',
    inPlayNote: (elapsed: number | null) =>
      elapsed === null
        ? 'Este partido estaba en curso en la última sincronización. No es un marcador en vivo.'
        : `Este partido estaba en curso en la última sincronización (minuto ${elapsed} registrado). No es un marcador en vivo.`,
    detailsTitle: 'Datos del partido',
    kickoffChile: 'Inicio (hora de Chile)',
    kickoffUtc: 'Inicio (UTC)',
    competition: 'Competición',
    competitionFallback: (id: number) => `Competición n.º ${id}`,
    season: 'Temporada',
    round: 'Fecha o ronda',
    venue: 'Estadio',
    referee: 'Árbitro',
    statusCode: 'Código de estado',
    notRecorded: 'No registrado',
    syncNote:
      'Datos según la última sincronización con el proveedor. La API no informa la hora de esa sincronización, así que pueden no reflejar cambios posteriores.',
    moreTitle: 'Seguir explorando',
    moreCompetition: (name: string, season: number) => `Más partidos de ${name}, temporada ${season}`,
    moreTeam: (team: string) => `Partidos de ${team} en esta temporada`,
    researchTitle: 'Contexto de investigación',
    researchLead:
      'Qué puede y qué no puede decir PREDIKTIA sobre este partido con la evidencia disponible hoy.',
    modelOutput: 'Salida del modelo',
    abstainBody:
      'PREDIKTIA no está autorizada para publicar pronósticos certificados. Sus modelos son solo de investigación, por lo que para este partido no se muestra ninguna probabilidad, cuota, valor esperado ni recomendación.',
    whyAbstain: 'Por qué PREDIKTIA se abstiene',
    ft90Title: 'Qué dato usa la investigación de goles',
    ft90Available: (home: number, away: number) =>
      `La investigación de goles usa el marcador a 90 minutos, sin alargue ni penales. Para este partido está registrado: ${home}-${away}.`,
    ft90Missing:
      'La investigación de goles usa el marcador a 90 minutos, sin alargue ni penales. Ese dato solo existe para partidos finalizados (FT, AET o PEN) y no está registrado para este partido.',
    gatesTitle: 'Qué faltaría para un pronóstico',
    gatesLink: 'Ver los requisitos de evidencia',
    researchLink: 'Cómo funciona la investigación',
  },

  status: {
    TBD: 'Horario por definir',
    NS: 'Programado',
    '1H': 'Primer tiempo',
    HT: 'Entretiempo',
    '2H': 'Segundo tiempo',
    ET: 'Alargue',
    BT: 'Descanso antes del alargue',
    P: 'Definición a penales',
    LIVE: 'En juego',
    FT: 'Finalizado',
    AET: 'Finalizado tras alargue',
    PEN: 'Finalizado en penales',
    AWD: 'Resultado adjudicado',
    WO: 'Walkover (W.O.)',
    PST: 'Postergado',
    SUSP: 'Suspendido',
    INT: 'Interrumpido',
    CANC: 'Cancelado',
    ABD: 'Abandonado',
  } as Readonly<Record<string, string>>,

  scoreKinds: {
    final: 'Resultado final',
    lastSync: 'Marcador en la última sincronización',
    fulltime: '90 minutos',
    halftime: 'Primer tiempo',
    extratime: 'Alargue',
    penalties: 'Penales',
  },

  research: {
    documentTitle: 'Investigación',
    eyebrow: 'Investigación',
    title: 'Enfoque de investigación cuantitativa',
    lead: 'Cómo estudia PREDIKTIA los resultados del fútbol, y por qué nada de esa investigación se presenta como pronóstico.',
    noticeTitle: 'Investigar no es pronosticar',
    notice:
      'Los modelos de investigación son experimentos evaluados con datos históricos. Sus diagnósticos ayudan a decidir si vale la pena seguir un enfoque; no certifican ningún pronóstico. Esta página no contiene probabilidades, cifras de acierto ni estadísticas de rendimiento.',
    pipelineTitle: 'Cómo funciona la investigación',
    pipeline: [
      {
        title: 'Ingerir y normalizar',
        body: 'Los datos del proveedor pasan por un adaptador hacia el modelo propio de PREDIKTIA. La semántica de cada proveedor queda en su adaptador; los marcadores se guardan como pares completos y nunca a medias.',
      },
      {
        title: 'Construir variables en su punto en el tiempo',
        body: 'Las entradas se reconstruyen «a la fecha» de un corte anterior al inicio. Si no se puede demostrar que un dato estaba disponible en ese momento, se trata como desconocido.',
      },
      {
        title: 'Partir de referencias transparentes',
        body: 'Referencias simples y bien entendidas, como un modelo de Poisson independiente para los goles, fijan la vara que cualquier modelo más complejo debe superar.',
      },
      {
        title: 'Modelar distribuciones completas',
        body: 'Los modelos describen la distribución de goles a 90 minutos, y de ella se proyectan de forma coherente cantidades de mercado (por ejemplo, líneas de más/menos goles o «ambos marcan») en vez de modelarlas por separado.',
      },
      {
        title: 'Evaluar fuera de muestra',
        body: 'El desempeño se mide en partidos que el modelo nunca vio, con reglas de puntuación propias, pendientes de calibración y controles de estabilidad en el tiempo.',
      },
      {
        title: 'Llegar a un veredicto con evidencia',
        body: 'Los diagnósticos terminan en un veredicto explícito. Si la evidencia es insuficiente o no concluyente, el veredicto es abstenerse.',
      },
    ],
    comparisonTitle: 'Modelo de investigación frente a pronóstico certificado',
    comparisonLead: 'Son productos distintos, con estándares de prueba distintos. Hoy solo existe el primero.',
    comparisonRegion: 'Tabla comparativa, desplazable',
    comparisonCaption: 'Comparación entre modelos de investigación y pronósticos certificados',
    comparisonAspect: 'Aspecto',
    comparisonResearch: 'Modelo de investigación',
    comparisonCertified: 'Pronóstico certificado',
    comparison: [
      { aspect: 'Propósito', research: 'Aprender si un enfoque de modelado funciona', certified: 'Informar una decisión real sobre un partido futuro' },
      {
        aspect: 'Régimen de datos',
        research: 'Pruebas retrospectivas, en parte reconstruidas a posteriori',
        certified: 'Entradas estrictamente en su punto en el tiempo, disponibles antes del inicio',
      },
      { aspect: 'Prueba de momento', research: 'No se exige', certified: 'Emisión autenticada de forma independiente antes del inicio' },
      { aspect: 'Validación', research: 'Diagnósticos sobre muestras pasadas', certified: 'Validación prospectiva y todos los requisitos de evidencia cumplidos' },
      { aspect: 'Visibilidad', research: 'Interna; aquí solo se resume en forma cualitativa', certified: 'Se publica solo tras una autorización explícita' },
      { aspect: 'Estado actual', research: 'Activo', certified: 'No existe: no está autorizado' },
    ],
    limitationsTitle: 'Limitaciones actuales',
    limitations: [
      'Ningún resultado de modelo está certificado y ninguno está autorizado para su publicación.',
      'Parte de las pruebas históricas usa datos cuya disponibilidad se reconstruyó a posteriori; se etiquetan como retrospectivas y no prueban lo que se sabía en su momento.',
      'No existe una fuente autenticada y con sello de tiempo de cuotas de casas de apuestas, así que ninguna comparación con el mercado ni cifra de valor esperado es confiable, y no se muestra ninguna.',
      'Los diagnósticos de calibración describen muestras pasadas. Pueden mostrar que un modelo está descalibrado, pero no certificar que sus pronósticos futuros lo estarán.',
      'Los partidos definidos en alargue o penales se excluyen de la investigación de goles a 90 minutos, así que esos desenlaces no se modelan.',
      'Algunas entradas, como las estadísticas de goles esperados, tienen cobertura histórica limitada y se tratan como opcionales.',
    ],
    statusTitle: 'Estado de la plataforma',
    detailsTitle: '¿Quieres más detalle?',
    detailsBefore: 'La ',
    detailsLink: 'página de metodología',
    detailsAfter:
      ' explica la procedencia de los datos, la validación, los límites de la calibración y la evidencia necesaria antes de poder publicar algo.',
  },

  methodology: {
    documentTitle: 'Metodología',
    eyebrow: 'Metodología',
    title: 'Metodología y evidencia',
    lead: 'Los estándares que PREDIKTIA se exige, lo que está y no está establecido, y por qué la respuesta por defecto es abstenerse.',
    tocTitle: 'En esta página',
    sections: {
      procedencia: 'Datos históricos y procedencia',
      'punto-en-el-tiempo': 'Disciplina del punto en el tiempo',
      validacion: 'Validación de modelos',
      calibracion: 'Límites de la calibración',
      'solo-investigacion': 'Estado de solo investigación',
      requisitos: 'Requisitos de evidencia',
      abstencion: 'Comportamiento ABSTAIN primero',
    },
    provenance: [
      'Competiciones, equipos, partidos y resultados se obtienen de proveedores externos de datos de fútbol. Cada proveedor tiene un adaptador que traduce su formato al modelo de datos propio de PREDIKTIA, para que la semántica del proveedor nunca llegue al código de investigación. Los datos fluyen en una sola dirección: proveedor, adaptador, modelo interno, servicio, repositorio y base de datos.',
    ],
    provenanceList: [
      'Los marcadores se guardan como pares local/visita completos. Una respuesta parcial o vacía del proveedor nunca sobrescribe un marcador conocido.',
      'El marcador a 90 minutos se guarda aparte del resultado final, porque los mercados que se liquidan al final del tiempo reglamentario no incluyen alargue ni penales.',
      'Cada observación se registra con el momento en que se vio, para poder auditar la historia de un partido.',
    ],
    pointInTime: [
      'Un pronóstico solo es honesto si usa información que existía cuando se hizo. Por eso las variables de investigación se reconstruyen «a la fecha» de un corte anterior al inicio. Todo lo que no se pueda demostrar disponible en ese momento se trata como desconocido.',
      'Parte del registro histórico se completó a posteriori. La investigación que lo usa se etiqueta como prueba retrospectiva: sirve para aprender, pero no prueba lo que un modelo podría haber sabido en tiempo real.',
    ],
    validation: [
      'Los modelos se comparan con referencias transparentes y se evalúan en partidos con los que no fueron ajustados. La evaluación usa reglas de puntuación propias, que premian probabilidades honestas y no audaces, junto con controles de estabilidad en el tiempo.',
      'Superar estos diagnósticos convierte un enfoque en candidato para seguir trabajando. No convierte ninguno de sus resultados en pronóstico.',
    ],
    calibration:
      'La calibración pregunta si los eventos a los que se asigna cierta probabilidad ocurren aproximadamente con esa frecuencia. Diagnósticos como las pendientes de calibración describen cómo se comportó un modelo en una muestra pasada, y tienen límites importantes:',
    calibrationList: [
      'Son estimaciones con incertidumbre, y las muestras pequeñas pueden ocultar o exagerar una descalibración.',
      'La calibración puede derivar a medida que cambian competiciones, equipos y fuentes de datos.',
      'Una buena calibración histórica no certifica que los pronósticos futuros estarán calibrados.',
    ],
    researchOnlyTitle: 'Sin pronósticos certificados',
    researchOnly:
      'Hoy PREDIKTIA no tiene autorización para mostrar pronósticos certificados. Esta interfaz no muestra probabilidades, cuotas, valores esperados, montos de apuesta, recomendaciones ni cifras de acierto de modelos, y los diagnósticos de investigación nunca se presentan como pronósticos.',
    evidenceLead: 'Antes de poder publicar cualquier pronóstico, debe existir cada uno de los siguientes elementos. Hoy no existe ninguno.',
    abstainLead:
      'Abstenerse es el resultado por defecto, no una falla. Un pronóstico solo se mostraría si se superaran todos los filtros siguientes; basta con fallar uno para que la salida sea ABSTAIN.',
    gates: [
      { question: '¿Todas las entradas están certificadas como conocidas antes del inicio?', outcome: 'Si no está demostrado: ABSTAIN.' },
      { question: '¿El modelo se validó fuera de muestra y de forma prospectiva?', outcome: 'Si no: ABSTAIN.' },
      { question: '¿La calibración es aceptable en datos recientes y pertinentes?', outcome: 'Si se desconoce o es mala: ABSTAIN.' },
      { question: '¿La hora de emisión está autenticada de forma independiente?', outcome: 'Si no: ABSTAIN.' },
      { question: '¿La publicación está autorizada explícitamente?', outcome: 'Si no: ABSTAIN.' },
    ],
    abstainBefore: 'Hoy todos los partidos resultan en ABSTAIN. Puedes comprobarlo en cualquier partido del ',
    abstainLink: 'listado de partidos',
    abstainAfter: '.',
  },

  notFound: {
    eyebrow: 'Error 404',
    title: 'Página no encontrada',
    lead: 'La página que buscas no existe o cambió de dirección.',
    home: 'Ir al inicio',
    fixtures: 'Ver partidos',
  },

  routeError: {
    documentTitle: 'Algo salió mal',
    title: 'Algo salió mal',
    stateTitle: 'Esta página no se pudo cargar',
    httpStatus: (status: number) => `La página no se pudo cargar (HTTP ${status}).`,
    generic: 'Ocurrió un error inesperado al mostrar esta página.',
    reload: 'Recargar la página',
    home: 'Volver al inicio',
  },
};

export type Messages = typeof esCL;
