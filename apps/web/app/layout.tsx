import type { ReactNode } from "react";

export const metadata = { title: "DataPilot AI", description: "Governed analytics for SMEs" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", background: "#f6f7f9", color: "#1c2330" }}>
        {children}
      </body>
    </html>
  );
}
