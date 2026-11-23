import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { Empty, ErrorAlert, Icon, Loading, order, PageHead } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { useFreshItems } from '../../lib/motion';
import { testsApi, type TestSummary } from '../../lib/tests-api';

/** Drafts reopen where work continues. Finished tests open as the student sees them. */
export const openPath = (test: TestSummary) => (test.status === 'draft' ? `/tests/${test.id}/structure` : `/tests/${test.id}/preview`);

export function TestListPage() {
  const { t, formatDate } = useI18n();
  const tests = useQuery({ queryKey: ['tests'], queryFn: testsApi.list });
  const listRef = useFreshItems<HTMLOListElement>(tests.data?.map((x) => x.id).join() ?? '');

  return (
    <>
      <PageHead eyebrow={t.tests.eyebrow} title={t.tests.title} lede={t.tests.lede}>
        <Link className="button-link auto" to="/tests/new" viewTransition>
          <span>{t.tests.create}</span>
          <Icon name="plus" />
        </Link>
      </PageHead>
      <section className="toc-wrap in" style={order(3)}>
        <ErrorAlert error={tests.error} />
        {tests.isPending && <Loading />}
        {tests.data?.length === 0 && (
          <Empty title={t.tests.emptyTitle}>{t.tests.emptyBody}</Empty>
        )}
        {tests.data?.length === 0 && (
          <Link className="button-link auto" to="/tests/new" viewTransition>
            <span>{t.tests.createFirst}</span>
            <Icon name="plus" />
          </Link>
        )}
        <ol className="toc" ref={listRef}>
          {tests.data?.map((test, i) => (
            <li key={test.id} data-key={test.id}>
              <Link className="toc-row" to={openPath(test)} viewTransition>
                <span className="toc-no" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                <span className="toc-name">
                  <span>{test.title}</span>
                  <span className="muted test-meta">{t.tests.meta(t.tests.types[test.testType], test.durationMinutes)}</span>
                </span>
                <span className="toc-leader" aria-hidden="true" />
                <span className={`tag${test.status === 'published' ? ' tag-live' : ''}`}>{t.tests.status[test.status]}</span>
                <span className="toc-date">{formatDate(test.createdAt)}</span>
                <span className="toc-arrow" aria-hidden="true"><Icon name="arrow" /></span>
              </Link>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
