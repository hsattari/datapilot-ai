import type { ReactNode } from "react";

const css = `
table{border-collapse:collapse;width:100%;font-size:14px}
th{text-align:left;color:#5b6575;font-weight:600;border-bottom:2px solid #e3e7ee;padding:8px 10px}
td{border-bottom:1px solid #eef0f4;padding:8px 10px;vertical-align:top}
button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:8px 14px;font:inherit;cursor:pointer}
button:hover:not(:disabled){background:#1d4ed8}
button:disabled{background:#dbe4f7;color:#35507f;cursor:default}
input:not([type=file]){display:block;width:100%;box-sizing:border-box;margin:0 0 10px;padding:9px 10px;border:1px solid #cfd6e2;border-radius:6px;font:inherit}
nav button{margin-right:8px}
code,pre{background:#f1f3f7;border-radius:4px;padding:2px 5px}
a{color:#2563eb}
`;

export const metadata = { title: "DataPilot AI", description: "Governed analytics for SMEs" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB">
      <head><style dangerouslySetInnerHTML={{ __html: css }} /></head>
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", background: "#f6f7f9", color: "#1c2330" }}>
        {children}
      </body>
    </html>
  );
}
