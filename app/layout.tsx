import type { Metadata } from "next";
import { OutboundClickTracking } from "./components/outbound-click-tracking";
import { EasterEggs } from "./components/easter-eggs";
import { SleepyFavicon } from "./components/sleepy-favicon";
import { RouteAnnouncer } from "./components/route-announcer";
import { SITE_ORIGIN } from "../lib/site";
import { shareCard } from "../lib/share-card";
import "./globals.css";
export const metadata: Metadata = {
  metadataBase: new URL(SITE_ORIGIN),
  title: {
    default: "fxcss — The Firefox theme toolkit",
    template: "%s · fxcss",
  },
  description:
    "Try, build, and test Firefox userChrome.css themes. Live CSS editing, visual comparisons, and compatibility checks for macOS, Windows, and Linux.",
  icons: {
    icon: [
      { url: "/assets/fxcss-logo.svg", type: "image/svg+xml", sizes: "any" },
    ],
  },
  ...shareCard({
    title: "fxcss — The Firefox theme toolkit",
    description: "Make Firefox your own. Try themes, edit CSS live, and keep every detail working.",
  }),
};
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <EasterEggs>
          <SleepyFavicon />
          <RouteAnnouncer />
          {children}
        </EasterEggs>
        <OutboundClickTracking />
        <script
          async
          src="https://scripts.simpleanalyticscdn.com/latest.js"
        />
      </body>
    </html>
  );
}
