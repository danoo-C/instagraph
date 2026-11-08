import { Navigate, useLocation } from "react-router-dom";

/** "/" → Overview (useApi redirects to /import on 409 when nothing is imported). */
export function RootRedirect() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: "/overview", search }} replace />;
}
