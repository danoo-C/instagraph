import { Link } from "react-router-dom";
import { EmptyState } from "@/components/EmptyState";
import { useT } from "@/i18n";

export default function NotFoundPage() {
  const t = useT();
  return (
    <div className="page">
      <EmptyState
        title={t("page.notFound")}
        action={
          <Link className="btn" to="/overview">
            {t("nav.overview")}
          </Link>
        }
      />
    </div>
  );
}
