import type { Metadata } from "next";
import { headers } from "next/headers";
import { CSPProvider } from "@base-ui/react/csp-provider";
import { SITE_ORIGIN } from "../lib/site";
import "./globals.css";
export const metadata: Metadata = {
  metadataBase: new URL(SITE_ORIGIN),
  title: {
    default: "fxcss — The Firefox theme toolkit",
    template: "%s · fxcss",
  },
  description:
    "Try, build, and test Firefox userChrome.css themes. Live CSS editing, visual comparisons, and compatibility checks for macOS, Windows, and Linux.",
  icons: { icon: "/assets/icon.png" },
  openGraph: {
    type: "website",
    siteName: "fxcss",
    title: "fxcss — The Firefox theme toolkit",
    description: "Make Firefox your own. Try themes, edit CSS live, and keep every detail working.",
  },
  twitter: {
    card: "summary",
    title: "fxcss — The Firefox theme toolkit",
    description: "Try, build, and test Firefox userChrome.css themes.",
  },
};
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const policy = (await headers()).get("content-security-policy");
  const nonce = policy?.match(/'nonce-([^']+)'/)?.[1];
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <CSPProvider nonce={nonce}>{children}</CSPProvider>
      </body>
    </html>
  );
}
