import type { Metadata } from 'next';
import { ShieldCheck } from 'lucide-react';
import { SiteHeader, Footer, REPO } from '../components/site-chrome';
import { shareCard } from '../../lib/share-card';

export const metadata: Metadata = {
  title: 'Website data',
  description:
    'Every service fxcss.com uses: Cloudflare for its domain name and hosting, and Simple Analytics for visit counts, and what each one receives.',
  alternates: { canonical: '/open' },
  ...shareCard({ url: '/open' }),
};

export default function WebsiteData() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="data-notice content-width">
        <h1>Website data notice</h1>
        <p className="data-notice-meta">Updated 3 October 2026</p>

        <div className="guide-callout">
          <ShieldCheck size={20} />
          <p>
            <strong>In short:</strong> fxcss.com aims to collect as little data
            as possible, and to be open about the data it does collect. This
            page lists every service the site uses, what each one does, and
            where your data is kept. It covers only this website. The fxcss tool
            is separate: it runs on your computer and sends nothing to us.
          </p>
        </div>

        <div className="doc-body">
          <h2 id="services">Services this website uses</h2>
          <dl className="service-list">
            <div>
              <dt>Domain name (DNS)</dt>
              <dd>
                <strong>Cloudflare.</strong> Answers when your browser looks up
                fxcss.com.{' '}
                <a href="https://www.cloudflare.com/privacypolicy/">
                  Cloudflare’s privacy policy
                </a>
              </dd>
            </div>
            <div>
              <dt>Hosting</dt>
              <dd>
                <strong>Cloudflare Workers.</strong> Runs the site’s code on
                Cloudflare’s network, in the data centre nearest to you,
                delivers its pages and files, including the setup script, and
                protects the site from attacks. Cloudflare receives the details
                it needs to deliver each page: your Internet Protocol (IP)
                address, browser details, the page you asked for and the time.
                It keeps a log of the requests the site’s code handles, and of
                any errors, for up to 7 days.
              </dd>
            </div>
            <div>
              <dt>Visit counts</dt>
              <dd>
                <strong>Simple Analytics.</strong> Counts page visits and clicks
                on links to other websites, without cookies (small files a
                website can keep in your browser). We see only totals, such as
                pages visited, the websites that linked here, the outside links
                people chose (without anything after a ? or # in the address),
                countries, browsers and device types. It does not count browsers
                set to send a Do Not Track request.{' '}
                <a href="https://www.simpleanalytics.com/privacy-policy">
                  Simple Analytics privacy policy
                </a>
              </dd>
            </div>
          </dl>

          <h2 id="storage">How your data is stored</h2>
          <p>
            The website has no forms, accounts or database, so it stores nothing
            you type. Searching the documentation happens in your browser, and
            what you search for is not sent anywhere.
          </p>
          <p>
            Simple Analytics keeps the visit counts in the European Union. They
            contain no personal data, so they hold nothing about you to show,
            correct or delete.
          </p>

          <h2 id="open-statistics">Open statistics</h2>
          <p>
            We believe in making data open where we can. The site’s visit
            counts are public, so you can see exactly what we see.
          </p>
          <p>
            <a href="https://dashboard.simpleanalytics.com/fxcss.com">
              fxcss.com’s public visit statistics on Simple Analytics
            </a>
          </p>

          <h2 id="cookies">Cookies and browser storage</h2>
          <p>The site sets no cookies, and neither does Simple Analytics.</p>
          <p>
            The site’s code can keep one short note in your browser’s storage
            for the current tab. It is called{' '}
            <code>__vinext_hard_navigation_target__</code> when a page has to
            load in full instead of updating in place, or{' '}
            <code>__vinext_rsc_initial_reload__</code> when a page has to reload
            after an error. It holds the address of that page on this site, so
            the page cannot get stuck reloading. It is removed as soon as the
            page has loaded, and it is never sent to us.
          </p>

          <h2 id="not-on-this-site">What is not on this site</h2>
          <p>
            fxcss.com has no advertising and no social media tracking buttons.
            It has no advertising pixels, which are tiny hidden images that
            report your visit to advertisers. It has no trackers that follow you
            to other websites. Apart from its own files, the site loads only
            Simple Analytics.
          </p>
          <p>
            JavaScript is the code that makes pages interactive. If it is turned
            off in your browser, Simple Analytics does not count your visit,
            because the site has no fallback counter that works without it.
          </p>

          <h2 id="fxcss-tool">The fxcss tool</h2>
          <p>
            fxcss runs on your own computer. It sends nothing to us, and none of
            the website’s services see what you do with it.
          </p>
          <p>
            fxcss itself connects only to GitHub, and only when you ask it to
            work with a theme stored there: <code>fxcss try</code>,{' '}
            <code>fxcss install</code> and <code>fxcss upgrade</code> download
            the theme, and <code>fxcss adopt</code> and{' '}
            <code>fxcss profiles --check</code> look up its versions. It never
            checks for its own updates, and it turns off Firefox’s data
            reporting in the temporary profiles it creates.
          </p>
          <p>
            The setup script installs fxcss from the Python Package Index (PyPI)
            using pipx, a tool that installs Python programs. GitHub and PyPI
            handle those downloads under{' '}
            <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">
              GitHub’s privacy statement
            </a>{' '}
            and{' '}
            <a href="https://policies.python.org/pypi.org/Privacy-Notice/">
              PyPI’s privacy notice
            </a>
            .
          </p>

          <h2 id="more-detail">More detail</h2>
          <p>
            This page is the full privacy statement for fxcss.com. The website
            has no forms or accounts, so it holds nothing you have sent us. If
            you have a question about this page,{' '}
            <a href={`${REPO}/issues`}>open an issue on GitHub</a>. Issues are
            public, so leave out anything personal.
          </p>
        </div>
      </main>
      <Footer />
    </>
  );
}
