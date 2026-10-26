import { Link } from "react-router";
import { BellRing, Radar, CircleSlash } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SiteHeader } from "@/components/SiteHeader";
import { Reveal } from "@/components/Reveal";
import { Botanicals } from "@/components/Botanicals";
import { usePageMeta } from "@/hooks/use-page-meta";

const features = [
  { icon: Radar, title: "盯緊熱門航線", en: "Always-on route watching", body: "持續監控台北出發的熱門航線（東京、首爾），自動抓最低票價。" },
  { icon: BellRing, title: "達標自動通知", en: "Target-price email alerts", body: "低於你設定的目標價，就寄 email 提醒你，附上立即訂購連結。" },
  { icon: CircleSlash, title: "隨時取消", en: "Cancel anytime", body: "月訂閱制(300 NTD/月)，不想用隨時停，沒有綁約。" },
];

// Icon chip colour per card, like differently coloured pressed flowers.
const chipColors = ["bg-leaf", "bg-marigold", "bg-periwinkle"];

export default function Index() {
  usePageMeta("Flight Price Notifier — 機票降價通知", "Set a route and a target price — we email you when the fare drops.");
  return (
    <div className="min-h-screen">
      <SiteHeader
        right={
          <Button asChild variant="hero" size="sm">
            <Link to="/signin">Sign in / 登入</Link>
          </Button>
        }
      />
      <section className="relative overflow-hidden bg-hero">
        <Botanicals className="absolute -left-10 bottom-0 hidden w-56 md:block lg:w-72" />
        <Botanicals variant="b" className="absolute -right-10 bottom-0 hidden w-56 md:block lg:w-72" />
        <div className="relative mx-auto max-w-4xl px-5 pb-24 pt-24 text-center sm:pt-32">
          <p className="animate-fade-up inline-block -rotate-2 rounded-sm border-2 border-foreground bg-card px-3 py-1 text-sm font-bold uppercase tracking-[0.2em] shadow-glow">機票降價通知</p>
          <h1 className="animate-fade-up mt-6 text-5xl uppercase tracking-tight sm:text-7xl" style={{ animationDelay: "80ms" }}>
            Flight Price <span className="text-gradient">Notifier</span>
          </h1>
          <p className="animate-fade-up mt-6 text-xl font-medium sm:text-2xl" style={{ animationDelay: "160ms" }}>
            設定航線與目標價，機票降價就通知你
          </p>
          <p className="animate-fade-up mt-3 text-muted-foreground" style={{ animationDelay: "220ms" }}>
            Set a route and a target price — we email you when the fare drops.
          </p>
          <div className="animate-fade-up mt-10" style={{ animationDelay: "300ms" }}>
            <Button asChild variant="hero" size="lg">
              <Link to="/signup">免費開始 Get started</Link>
            </Button>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-20">
        <div className="grid gap-5 md:grid-cols-3">
          {features.map((f, i) => (
            <Reveal key={f.en} delay={i * 100}>
              <article className="specimen h-full rounded-2xl p-7 transition-transform hover:-translate-y-1 hover:rotate-[-0.5deg]">
                <span className={`grid h-11 w-11 place-items-center rounded-full border-2 border-foreground text-foreground ${chipColors[i]}`}>
                  <f.icon className="h-5 w-5" />
                </span>
                <h2 className="mt-5 text-lg font-bold">{f.title}</h2>
                <p className="text-sm font-semibold text-primary-glow">{f.en}</p>
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{f.body}</p>
              </article>
            </Reveal>
          ))}
        </div>
      </section>

      <footer className="border-t-2 border-border py-8 text-center text-sm text-muted-foreground">
        © 2026 Flight Price Notifier
      </footer>
    </div>
  );
}
