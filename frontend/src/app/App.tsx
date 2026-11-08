import { useState } from "react";
import { RouterProvider } from "react-router-dom";
import { Providers } from "@/app/providers";
import { createAppRouter } from "@/app/router";

export function App() {
  const [router] = useState(createAppRouter);
  return (
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  );
}
