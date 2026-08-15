import { Link } from 'react-router-dom';

import { EmptyState } from '../components/StateViews';
import { useI18n } from '../i18n';

export default function NotFoundPage() {
  const { t } = useI18n();

  return (
    <EmptyState
      title={t('notFound.title')}
      description={t('notFound.description')}
      action={
        <Link to="/" className="btn btn-primary">
          {t('detail.back')}
        </Link>
      }
    />
  );
}
