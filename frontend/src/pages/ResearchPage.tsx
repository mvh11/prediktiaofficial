import { Link } from 'react-router';
import { PATHS } from '../components/layout/navigation.ts';
import { CapabilityList } from '../components/ui/CapabilityList.tsx';
import { Card } from '../components/ui/Card.tsx';
import { Notice } from '../components/ui/Notice.tsx';
import { PageHeader } from '../components/ui/PageHeader.tsx';
import { ProductCodes } from '../components/ui/ProductCodes.tsx';
import { useMessages } from '../i18n/i18n.ts';
import styles from './ContentPage.module.css';
import { cx } from '../lib/cx.ts';

export function ResearchPage() {
  const m = useMessages();
  const r = m.research;
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: m.nav.home, to: PATHS.home }, { label: m.nav.research }]}
        eyebrow={r.eyebrow}
        title={r.title}
        documentTitle={r.documentTitle}
        lead={<p>{r.lead}</p>}
      />

      <div className={cx('container', styles.body)}>
        <Notice tone="warning" title={r.noticeTitle}>
          <p>{r.notice}</p>
        </Notice>

        <section aria-labelledby="codes-title" className={styles.section}>
          <h2 id="codes-title">{m.productCodes.title}</h2>
          <ProductCodes />
        </section>

        <section aria-labelledby="pipeline-title" className={styles.section}>
          <h2 id="pipeline-title">{r.pipelineTitle}</h2>
          <ol className={styles.steps}>
            {r.pipeline.map((step) => (
              <li key={step.title} className={styles.step}>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section aria-labelledby="comparison-title" className={styles.section}>
          <h2 id="comparison-title">{r.comparisonTitle}</h2>
          <p className="muted prose">{r.comparisonLead}</p>
          {/* Scrollable on narrow screens: focusable so keyboard users can scroll it. */}
          <div className={styles.tableWrap} role="region" aria-label={r.comparisonRegion} tabIndex={0}>
            <table className={styles.table}>
              <caption className="visually-hidden">{r.comparisonCaption}</caption>
              <thead>
                <tr>
                  <th scope="col">{r.comparisonAspect}</th>
                  <th scope="col">{r.comparisonResearch}</th>
                  <th scope="col">{r.comparisonCertified}</th>
                </tr>
              </thead>
              <tbody>
                {r.comparison.map((row) => (
                  <tr key={row.aspect}>
                    <th scope="row">{row.aspect}</th>
                    <td>{row.research}</td>
                    <td>{row.certified}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-labelledby="limitations-title" className={styles.section}>
          <h2 id="limitations-title">{r.limitationsTitle}</h2>
          <ul className={cx(styles.checklist, 'prose')}>
            {r.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="status-title" className={styles.section}>
          <h2 id="status-title">{r.statusTitle}</h2>
          <CapabilityList />
        </section>

        <Card variant="muted" title={r.detailsTitle} headingLevel={2}>
          <p>
            {r.detailsBefore}
            <Link to={PATHS.methodology}>{r.detailsLink}</Link>
            {r.detailsAfter}
          </p>
        </Card>
      </div>
    </>
  );
}
