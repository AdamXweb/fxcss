'use client';
import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Check, CodeXml, ShieldCheck } from 'lucide-react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { CopyCommand } from './copy-command';
import { SITE_ORIGIN } from '../../lib/site';
type Journey = 'use' | 'build' | 'maintain';
function Step({
  number,
  title,
  description,
  command,
}: {
  number: string;
  title: string;
  description: string;
  command: string;
}) {
  return (
    <section className="guide-step" id={`step-${number}`}>
      <span className="step-number">{number}</span>
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
        <CopyCommand command={command} />
      </div>
    </section>
  );
}
function InstallStep({ journey }: { journey: Journey }) {
  return (
    <section className="guide-step" id="step-01">
      <span className="step-number">01</span>
      <div>
        <h2>Install fxcss</h2>
        <p>
          Choose one method. Both include the image tools; then continue to step
          02.
        </p>
        <div className="guide-install-options">
          <div>
            <h3>Guided setup · macOS and Linux</h3>
            <p>
              Installs pipx if needed and confirms the plan for your chosen
              path.
            </p>
            <CopyCommand
              command={`curl -fsSL ${SITE_ORIGIN}/install.sh | bash -s -- --intent ${journey}`}
            />
            <Link
              href="/install.sh"
              target="_blank"
              rel="noreferrer"
              prefetch={false}
            >
              Read the setup script
            </Link>
          </div>
          <div>
            <h3>Manual · pipx already installed</h3>
            <p>Use pipx directly on macOS, Linux, or Windows.</p>
            <CopyCommand command={'pipx install "fxcss[images]"'} />
            <Link href="/docs/installation">
              Need pipx first? See installation options
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
export function GettingStarted({
  initialJourney = 'use',
}: {
  initialJourney?: Journey;
}) {
  const [journey, setJourney] = useState<Journey>(initialJourney);
  function selectJourney(value: string) {
    if (value !== 'use' && value !== 'build' && value !== 'maintain') return;
    setJourney(value);
    window.history.replaceState(
      null,
      '',
      value === 'use' ? '/docs' : `/docs?path=${value}`,
    );
  }
  return (
    <>
      <div className="breadcrumbs">
        Documentation <span>/</span> Start here
      </div>
      <span className="eyebrow">THE FIELD GUIDE</span>
      <h1>
        A Firefox that feels
        <br />
        like yours.
      </h1>
      <p className="guide-lead">
        Try, build, and maintain Firefox themes. <br />
        Pick a starting point. You’ll be working in a few commands.
      </p>
      <div className="guide-prerequisites">
        <span>
          <Check size={14} /> Python 3.9+
        </span>
        <span>
          <Check size={14} /> Firefox installed
        </span>
        <span>
          <Check size={14} /> macOS, Windows or Linux
        </span>
      </div>
      <div className="guide-rule" />
      <Tabs
        className="journey-tabs"
        value={journey}
        onValueChange={(v) => selectJourney(String(v))}
      >
        <TabsList aria-label="Getting started path" variant="line">
          <TabsTrigger value="use">Use a theme</TabsTrigger>
          <TabsTrigger value="build">Build a theme</TabsTrigger>
          <TabsTrigger value="maintain">Maintain a theme</TabsTrigger>
        </TabsList>
        <TabsContent value="use">
          <InstallStep journey="use" />
          <Step
            number="02"
            title="Take a theme for a spin"
            description="Try WhiteSur in a disposable profile. Your everyday browser stays unchanged."
            command="fxcss try AdamXweb/WhiteSurFirefoxThemeMacOS"
          />
          <div className="guide-callout">
            <ShieldCheck size={20} />
            <div>
              <strong>Just exploring? That’s the idea.</strong>
              <p>
                Close the preview window when you’re done. Install into your
                real profile when you’re ready.
              </p>
            </div>
          </div>
        </TabsContent>
        <TabsContent value="build">
          <InstallStep journey="build" />
          <Step
            number="02"
            title="Start with a working theme"
            description="Create a starter, open its folder, then watch for saved edits."
            command={'fxcss new my-theme\ncd my-theme\nfxcss watch'}
          />
          <div className="guide-callout">
            <CodeXml size={20} />
            <div>
              <strong>Save an edit. See the result.</strong>
              <p>
                Edit chrome/userChrome.css while watch is running. Firefox
                reloads the styles.
              </p>
            </div>
          </div>
        </TabsContent>
        <TabsContent value="maintain">
          <InstallStep journey="maintain" />
          <Step
            number="02"
            title="Generate your workflows"
            description="Run from your theme repository to add PR previews, Firefox checks, and release images."
            command="fxcss init --watch --showcase"
          />
          <div className="guide-callout">
            <ShieldCheck size={20} />
            <div>
              <strong>Review the generated workflows.</strong>
              <p>
                Commit them to your theme repository to begin running checks on
                GitHub.
              </p>
            </div>
          </div>
        </TabsContent>
      </Tabs>
      <div className="guide-bottom-links">
        <Link href="/docs/installation">Installation options</Link>
        <Link
          href={`/docs/${journey === 'build' ? 'watch' : journey === 'maintain' ? 'init' : 'install'}`}
        >
          Keep going <ArrowRight size={16} />
        </Link>
      </div>
    </>
  );
}
