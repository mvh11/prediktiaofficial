import { Link } from 'react-router';
import { PATHS } from '../components/layout/navigation.ts';
import { Notice } from '../components/ui/Notice.tsx';
import { PageHeader } from '../components/ui/PageHeader.tsx';
import { StatusBadge } from '../components/ui/StatusBadge.tsx';
import { EVIDENCE_REQUIREMENTS } from '../content/productStatus.ts';
import { useMessages } from '../i18n/i18n.ts';
import type { Messages } from '../i18n/messages.es-CL.ts';
import styles from './ContentPage.module.css';
import { cx } from '../lib/cx.ts';

type SectionId = keyof Messages['methodology']['sections'];

/** In-page anchors (Spanish, stable: linked from fixture pages). */
const SECTION_IDS: readonly SectionId[] = [
  'procedencia',
  'punto-en-el-tiempo',
  'validacion',
  'calibracion',
  'solo-investigacion',
  'requisitos',
  'abstencion',
];

export function MethodologyPage() {
  const m = useMessages();
  const t = m.methodology;
  const heading = (id: SectionId) => <h2 id={`${id}-title`}>{t.sections[id]}</h2>;
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: m.nav.home, to: PATHS.home }, { label: m.nav.methodology }]}
        eyebrow={t.eyebrow}
        title={t.title}
        documentTitle={t.documentTitle}
        lead={<p>{t.lead}</p>}
      />

      <div className={cx('container', styles.body)}>
        <div className={styles.layout}>
          <nav className={styles.toc} aria-labelledby="toc-title">
            <h2 id="toc-title">{t.tocTitle}</h2>
            <ol>
              {SECTION_IDS.map((id) => (
                <li key={id}>
                  <a href={`#${id}`}>{t.sections[id]}</a>
                </li>
              ))}
            </ol>
          </nav>

          <article className={styles.article}>
            <section id="procedencia" aria-labelledby="procedencia-title">
              {heading('procedencia')}
              {t.provenance.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
              <ul>
                {t.provenanceList.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>

            <section id="punto-en-el-tiempo" aria-labelledby="punto-en-el-tiempo-title">
              {heading('punto-en-el-tiempo')}
              {t.pointInTime.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
            </section>

            <section id="validacion" aria-labelledby="validacion-title">
              {heading('validacion')}
              {t.validation.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
            </section>

            <section id="calibracion" aria-labelledby="calibracion-title">
              {heading('calibracion')}
              <p>{t.calibration}</p>
              <ul>
                {t.calibrationList.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>

            <section id="solo-investigacion" aria-labelledby="solo-investigacion-title">
              {heading('solo-investigacion')}
              <Notice tone="warning" title={t.researchOnlyTitle}>
                <p>{t.researchOnly}</p>
              </Notice>
            </section>

            <section id="requisitos" aria-labelledby="requisitos-title">
              {heading('requisitos')}
              <p>{t.evidenceLead}</p>
              <ul className={styles.requirements}>
                {EVIDENCE_REQUIREMENTS.map((item) => {
                  const text = m.evidence.items[item.id];
                  return (
                    <li key={item.id} className={styles.requirement}>
                      <h3>{text.requirement}</h3>
                      <StatusBadge tone="abstain">
                        {item.status === 'not_granted' ? m.evidence.statusNotGranted : m.evidence.statusNotEstablished}
                      </StatusBadge>
                      <p>{text.why}</p>
                    </li>
                  );
                })}
              </ul>
            </section>

            <section id="abstencion" aria-labelledby="abstencion-title">
              {heading('abstencion')}
              <p>{t.abstainLead}</p>
              <ol className={styles.flow}>
                {t.gates.map((gate) => (
                  <li key={gate.question}>
                    {gate.question}
                    <span className={styles.flowOutcome}>{gate.outcome}</span>
                  </li>
                ))}
              </ol>
              <p>
                {t.abstainBefore}
                <Link to={PATHS.fixtures}>{t.abstainLink}</Link>
                {t.abstainAfter}
              </p>
            </section>
          </article>
        </div>
      </div>
    </>
  );
}
