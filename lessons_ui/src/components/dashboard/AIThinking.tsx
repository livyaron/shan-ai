import { useEffect, useState } from "react";

// What the AI card shows while the model works: the dashboard postcard's
// infinity loop, traced in gold, with a spark running along it and a status
// line that walks through what is being read. Decoration only — it knows
// nothing about the reply and never claims progress it cannot measure.

const LOOP = "M 40 60 C 40 15 105 15 130 60 C 155 105 220 105 220 60 C 220 15 155 15 130 60 C 105 105 40 105 40 60 Z";

interface Props {
  lessonCount: number;
}

const AIThinking = ({ lessonCount }: Props) => {
  const steps = [
    `קורא ${lessonCount} לקחים…`,
    "מחפש דפוסים חוזרים…",
    "משווה בין פרויקטים ושלבים…",
    "מזהה סיכונים…",
    "מנסח המלצות פעולה…",
  ];
  const [step, setStep] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setStep((s) => (s + 1) % steps.length), 2200);
    return () => clearInterval(t);
  }, [steps.length]);

  return (
    <div className="relative overflow-hidden rounded-2xl bg-gradient-to-b from-[#16223b] to-[#22325a] px-6 py-8 text-center" role="status" aria-live="polite">
      <style>{`
        @keyframes ait-trace { to { stroke-dashoffset: -520; } }
        @keyframes ait-breathe { 0%,100% { opacity: .35 } 50% { opacity: .9 } }
        @keyframes ait-rise { from { transform: translateY(6px); opacity: 0 } to { transform: none; opacity: 1 } }
        .ait-trace { stroke-dasharray: 90 430; animation: ait-trace 2.6s linear infinite; }
        .ait-glow { animation: ait-breathe 2.6s ease-in-out infinite; }
        .ait-step { animation: ait-rise .45s ease-out both; }
        @media (prefers-reduced-motion: reduce) { .ait-trace, .ait-glow, .ait-step { animation: none; } }
      `}</style>
      <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.10]"
        style={{ backgroundImage: "radial-gradient(circle at 1px 1px, #f4e7c5 1px, transparent 0)", backgroundSize: "18px 18px" }} />
      <svg viewBox="0 0 260 120" className="relative mx-auto h-28 w-auto" aria-hidden>
        <defs>
          <filter id="ait-blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3" /></filter>
        </defs>
        <path d={LOOP} fill="none" stroke="#3a4c70" strokeWidth="10" strokeLinecap="round" />
        <path d={LOOP} fill="none" stroke="#e6a930" strokeWidth="2" opacity=".55" />
        <path className="ait-glow" d={LOOP} fill="none" stroke="#e6a930" strokeWidth="6" filter="url(#ait-blur)" />
        <path className="ait-trace" d={LOOP} fill="none" stroke="#fff2cf" strokeWidth="3.5" strokeLinecap="round" />
        <circle r="5" fill="#fff2cf">
          <animateMotion dur="2.6s" repeatCount="indefinite" path={LOOP} />
        </circle>
      </svg>
      <p key={step} className="ait-step relative mt-4 text-sm font-medium text-[#f4e7c5]">{steps[step]}</p>
      <div className="relative mx-auto mt-3 flex justify-center gap-1.5" aria-hidden>
        {steps.map((_, i) => (
          <span key={i} className={`h-1.5 rounded-full transition-all duration-500 ${i === step ? "w-6 bg-[#e6a930]" : "w-1.5 bg-[#f4e7c5]/30"}`} />
        ))}
      </div>
    </div>
  );
};

export default AIThinking;
