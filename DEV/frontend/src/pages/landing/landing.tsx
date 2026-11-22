import { useRef, useState, type MouseEvent } from 'react';
import { Link } from 'react-router';
import { DrawSVGPlugin } from 'gsap/DrawSVGPlugin';
import { ScrollToPlugin } from 'gsap/ScrollToPlugin';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SplitText } from 'gsap/SplitText';
import { STATIC_PREVIEW } from '../../lib/preview';
import { useI18n } from '../../lib/i18n';
import { gsap, reducedMotion, useGSAP } from '../../lib/motion';
import { Brand, Icon, LangSwitch } from '../../components/ui';
import { AuthorsFooter } from './authors';
import { LandingIntro, shouldPlayIntro } from './intro';
import { addRoadMotion, RoadMap } from './road-map';
import { TeacherMeme } from './teacher-meme';

gsap.registerPlugin(ScrollTrigger, ScrollToPlugin, SplitText, DrawSVGPlugin);

type Verdict = 'TRUE' | 'FALSE' | 'NOT GIVEN';

const VERDICTS: Verdict[] = ['TRUE', 'FALSE', 'NOT GIVEN'];

// The exam-style passage and statements stay in English in both languages.
const QUESTIONS: { statement: string; answer: Verdict; evidence: 'council' | 'customers' }[] = [
  { statement: 'The market moved indoors because of bad weather.', answer: 'NOT GIVEN', evidence: 'council' },
  { statement: 'Stallholders had more customers when the market was indoors.', answer: 'FALSE', evidence: 'customers' },
];

function jump(e: MouseEvent<HTMLAnchorElement>, id: string) {
  e.preventDefault();
  const target = document.getElementById(id);
  if (!target) return;
  if (reducedMotion()) target.scrollIntoView();
  else gsap.to(window, { scrollTo: { y: target, offsetY: 0 }, duration: 1.1, ease: 'power3.inOut' });
}

const HAND_SPOTS = ['16.66%', '50%', '83.33%'];

/** A cute hand taps through the three answers until the visitor picks one. It never selects anything. */
function TryHint({ text }: { text: string }) {
  const root = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    if (reducedMotion()) return;
    const options = gsap.utils.toArray<HTMLElement>(root.current?.closest('.try-panel')?.querySelectorAll('.try-option') ?? []);
    gsap.from('.try-hint-text', { scale: 0.5, y: 14, autoAlpha: 0, duration: 0.6, ease: 'back.out(2.2)' });
    gsap.to('.try-hint-text', { y: -4, duration: 1.1, repeat: -1, yoyo: true, ease: 'sine.inOut', delay: 0.6 });
    const tl = gsap.timeline({ repeat: -1, repeatDelay: 0.5 });
    HAND_SPOTS.forEach((left, i) => {
      tl.to('.try-hand', { left, duration: 0.5, ease: 'power3.inOut' }, i ? '+=0.25' : 0);
      for (let tap = 0; tap < 2; tap += 1) {
        tl.to('.try-hand-body', { y: -13, scale: 0.92, duration: 0.09, ease: 'power2.in' })
          .fromTo('.try-ripple', { scale: 0.2, opacity: 0.9 }, { scale: 1.7, opacity: 0, duration: 0.42, ease: 'power2.out' }, '<0.08')
          .to(options[i] ?? {}, { scale: 0.96, duration: 0.07, yoyo: true, repeat: 1, ease: 'power1.inOut' }, '<')
          .to('.try-hand-body', { y: 0, scale: 1, duration: 0.2, ease: 'back.out(3)' }, '<0.06');
      }
    });
    return () => { gsap.set(options, { clearProps: 'transform' }); };
  }, { scope: root });

  return (
    <div className="try-hint" ref={root}>
      <div className="try-hand" aria-hidden="true">
        <i className="try-ripple" />
        <svg className="try-hand-body" viewBox="0 0 72 92">
          <path className="hand-fill" d="M25 11a7 7 0 0 1 14 0v25a7 7 0 0 1 12 3 7 7 0 0 1 11 4 6 6 0 0 1 7 6v15c0 13-9 22-22 22H37c-8 0-14-4-18-11L5 53c-2-4-1-8 3-9 3-1 7 0 9 3l8 10z" />
          <circle className="hand-face" cx="36" cy="60" r="2.6" /><circle className="hand-face" cx="52" cy="60" r="2.6" />
          <path className="hand-smile" d="M39 67c2 4 8 4 10 0" />
          <circle className="hand-blush" cx="30" cy="67" r="3.4" /><circle className="hand-blush" cx="58" cy="67" r="3.4" />
        </svg>
      </div>
      <p className="try-hint-text">{text}</p>
    </div>
  );
}

function TryIt() {
  const { t } = useI18n();
  const copy = t.landing;
  const [index, setIndex] = useState(0);
  const [picked, setPicked] = useState<Verdict | null>(null);
  const q = QUESTIONS[index]!;
  const correct = picked === q.answer;
  const last = index === QUESTIONS.length - 1;
  const next = () => {
    setPicked(null);
    setIndex(last ? 0 : index + 1);
  };
  const lit = (key: 'council' | 'customers') => (picked && q.evidence === key ? ' is-lit' : '');

  return (
    <div className="try-grid">
      <article className="try-sheet" aria-label={copy.tryPassage}>
        <span className="try-tag">{copy.tryTag}</span>
        <h3>A morning at the market</h3>
        <p>Every Saturday, the town square fills with small stalls. Farmers arrive before sunrise to set out vegetables, fruit and fresh bread.</p>
        <p>Most visitors come for the food, but many stay to talk. For older residents, the market is the main place to meet friends during the week.</p>
        <p>
          <span className={`try-ev${lit('council')}`}>The council moved the market indoors for one winter.</span>{' '}
          <span className={`try-ev${lit('customers')}`}>Stallholders reported fewer customers,</span> and it returned to the square the following spring.
        </p>
      </article>
      <div className="try-panel">
        <p className="try-count">{copy.tryCount(index + 1, QUESTIONS.length)}</p>
        <p className="try-statement" key={q.statement}>{q.statement}</p>
        <div className="try-options" role="group" aria-label={copy.tryChoose}>
          {VERDICTS.map((v) => {
            const state = !picked ? '' : v === q.answer ? ' is-answer' : v === picked ? ' is-wrong' : ' is-off';
            return (
              <button key={v} type="button" className={`try-option${state}`} aria-pressed={picked === v} disabled={Boolean(picked)} onClick={() => setPicked(v)}>
                {v}
              </button>
            );
          })}
        </div>
        <div className="try-feedback" aria-live="polite">
          {!picked && <TryHint text={copy.tryHint} />}
          {picked && (
            <div className="try-note" key={`${index}-${picked}`}>
              <strong>{correct ? copy.tryCorrect : copy.tryWrong(q.answer)}</strong>
              <p>{copy.tryWhy[index]}</p>
              <button type="button" className="try-next" onClick={next}>
                <span>{last ? copy.tryAgain : copy.tryNext}</span>
                <Icon name="arrow" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function LandingPage() {
  const { t } = useI18n();
  const copy = t.landing;
  const root = useRef<HTMLDivElement>(null);
  const [showIntro, setShowIntro] = useState(shouldPlayIntro);
  // While the intro runs, the hero waits fully hidden and starts when the logo lands.
  const heroWaiting = useRef(showIntro);
  const heroEntrance = useRef<gsap.core.Timeline | null>(null);
  const heroSub = useRef<gsap.core.Tween | null>(null);
  const startHero = () => {
    heroWaiting.current = false;
    heroEntrance.current?.play();
    heroSub.current?.play();
  };
  const endIntro = () => {
    setShowIntro(false);
    ScrollTrigger.refresh();
  };

  useGSAP(() => {
    const mm = gsap.matchMedia(root);
    mm.add(
      { motion: '(prefers-reduced-motion: no-preference)', wide: '(min-width: 960px)', fine: '(hover: hover) and (pointer: fine)' },
      (ctx) => {
        const { motion, wide, fine } = ctx.conditions as { motion: boolean; wide: boolean; fine: boolean };
        if (!motion) return;

        // Hero entrance: headline rises, the highlighter lands, then the sheet gets marked.
        const intro = gsap.timeline({ paused: heroWaiting.current, defaults: { ease: 'expo.out' } });
        heroEntrance.current = intro;
        if (!heroWaiting.current) intro.from('.l-nav > *', { y: -14, autoAlpha: 0, duration: 0.8, stagger: 0.08 }, 0);
        intro
          .from('.hero-line > span', { yPercent: 112, duration: 1.15, stagger: 0.11 }, 0.08)
          .from('.hero-mark', { scaleX: 0, duration: 0.6, ease: 'power3.inOut' }, 0.5)
          .from('.hero-actions', { y: 20, autoAlpha: 0, duration: 0.9 }, 1)
          .from('.hero-sheet', { y: 90, rotate: 9, autoAlpha: 0, duration: 1.2 }, 0.2)
          .from('.hero-answers i.is-done, .hero-answers i.is-now', { scale: 0.4, autoAlpha: 0, duration: 0.4, stagger: 0.022, ease: 'back.out(2)' }, 0.75)
          .from('.hero-circle path', { drawSVG: '0%', duration: 0.65, ease: 'power2.inOut' }, 0.9)
          .from('.hero-arrow path', { drawSVG: '0%', duration: 0.35, ease: 'power2.out' }, 1.4)
          .from('.hero-note', { autoAlpha: 0, y: 8, duration: 0.45 }, 1.5);

        // Headline drifts slower than the page; the sheet straightens as you leave.
        gsap.to('.hero-copy', { yPercent: -14, ease: 'none', scrollTrigger: { trigger: '.l-hero', start: 'top top', end: 'bottom top', scrub: true } });
        gsap.to('.hero-stage', { yPercent: -10, rotate: -2, ease: 'none', scrollTrigger: { trigger: '.l-hero', start: 'top top', end: 'bottom top', scrub: true } });

        if (fine) {
          gsap.utils.toArray<HTMLElement>('.l-cta').forEach((cta) => {
            const mx = gsap.quickTo(cta, 'x', { duration: 0.5, ease: 'power3' });
            const my = gsap.quickTo(cta, 'y', { duration: 0.5, ease: 'power3' });
            cta.addEventListener('pointermove', (e) => {
              const b = cta.getBoundingClientRect();
              mx((e.clientX - b.left - b.width / 2) * 0.22);
              my((e.clientY - b.top - b.height / 2) * 0.32);
            });
            cta.addEventListener('pointerleave', () => { mx(0); my(0); });
          });
          const stage = root.current?.querySelector<HTMLElement>('.hero-stage');
          const hero = root.current?.querySelector<HTMLElement>('.l-hero');
          if (stage && hero) {
            const x = gsap.quickTo('.hero-tilt', 'x', { duration: 0.9, ease: 'power3' });
            const y = gsap.quickTo('.hero-tilt', 'y', { duration: 0.9, ease: 'power3' });
            const onMove = (e: PointerEvent) => {
              x((e.clientX / window.innerWidth - 0.5) * -16);
              y((e.clientY / window.innerHeight - 0.5) * -12);
            };
            hero.addEventListener('pointermove', onMove);
            return () => hero.removeEventListener('pointermove', onMove);
          }
        }

        return undefined;
      },
    );

    // Scroll story: three reads of one passage, pinned on wide screens.
    mm.add('(prefers-reduced-motion: no-preference) and (min-width: 960px)', () => {
      const story = root.current?.querySelector<HTMLElement>('.l-story');
      if (!story) return;
      story.dataset.step = '0';
      const setStep = (n: number) => { if (story.dataset.step !== String(n)) story.dataset.step = String(n); };
      const tl = gsap.timeline({
        defaults: { ease: 'power1.inOut' },
        scrollTrigger: {
          trigger: story,
          start: 'top top',
          end: '+=240%',
          pin: true,
          scrub: 0.6,
          anticipatePin: 1,
          onUpdate: (self) => {
            setStep(self.progress < 0.34 ? 0 : self.progress < 0.68 ? 1 : 2);
            gsap.set('.story-progress i', { scaleY: self.progress });
          },
        },
      });
      const page = story.querySelector<HTMLElement>('.story-page')!;
      const words = gsap.utils.toArray<HTMLElement>('.story-page .kw');
      const lensAt = (kw: HTMLElement) => {
        const p = page.getBoundingClientRect();
        const b = kw.getBoundingClientRect();
        return { x: b.left - p.left + b.width / 2, y: b.top - p.top + b.height / 2 };
      };
      tl.fromTo('.story-beam', { autoAlpha: 0, y: 0 }, { autoAlpha: 1, duration: 0.12 }, 0)
        .to('.story-beam', { y: () => page.offsetHeight - 3, duration: 1.3, ease: 'none' }, 0.05)
        .to('.story-beam', { autoAlpha: 0, duration: 0.14 }, 1.32)
        .to('.story-page .rest, .story-page .ev', { opacity: 0.3, duration: 1 }, 0.1)
        .to('.story-page .ev', { opacity: 1, duration: 0.6 }, 1.5)
        .fromTo('.story-lens', { autoAlpha: 0, scale: 0.4, x: () => lensAt(words[0]!).x - 60, y: () => lensAt(words[0]!).y + 50 }, { autoAlpha: 1, scale: 1, x: () => lensAt(words[0]!).x, y: () => lensAt(words[0]!).y, duration: 0.4, ease: 'back.out(1.8)' }, 1.5)
        .fromTo(words[0]!, { '--p': 0 }, { '--p': 1, duration: 0.45 }, 1.8)
        .to('.story-lens', { x: () => lensAt(words[1]!).x, y: () => lensAt(words[1]!).y, duration: 0.45 }, 2.3)
        .fromTo(words[1]!, { '--p': 0 }, { '--p': 1, duration: 0.45 }, 2.6)
        .to('.story-lens', { autoAlpha: 0, scale: 1.5, duration: 0.25 }, 3.05)
        .fromTo('.story-underline path', { drawSVG: '0%' }, { drawSVG: '100%', duration: 0.7 }, 3.2)
        .fromTo('.story-verdict', { autoAlpha: 0, scale: 2.6, rotate: 14 }, { autoAlpha: 1, scale: 1, rotate: -4, duration: 0.32, ease: 'power4.in' }, 3.85)
        .to(page, { keyframes: { x: [0, -7, 6, -3, 0], y: [0, 3, -2, 1, 0] }, duration: 0.25, ease: 'none' }, 4.16)
        .to({}, { duration: 0.6 });
    });

    addRoadMotion(mm, root);

    // Narrow screens and reduced motion get the finished page, never a half state.
    mm.add('(prefers-reduced-motion: reduce), (max-width: 959px)', () => {
      root.current?.querySelector('.l-story')?.classList.add('is-final');
      return () => root.current?.querySelector('.l-story')?.classList.remove('is-final');
    });

    // Every piece of text arrives as it scrolls in. These run after the pins so their positions are right.
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      const splits: SplitText[] = [];
      const seen = (trigger: Element) => ({ trigger, start: 'top 86%', once: true });

      gsap.utils.toArray<HTMLElement>('[data-split]').forEach((el) => {
        const split = SplitText.create(el, { type: 'words', mask: 'words', wordsClass: 'rv-w' });
        splits.push(split);
        const accent = split.words.slice(-Number(el.dataset.accent ?? 0) || split.words.length) as HTMLElement[];
        const tl = gsap.timeline({ scrollTrigger: seen(el) });
        tl.from(split.words, { yPercent: 118, rotate: 7, transformOrigin: '0% 100%', duration: 1.05, stagger: 0.075, ease: 'expo.out' });
        if (el.dataset.accent) tl.call(() => accent.forEach((w) => w.classList.add('is-accent')), undefined, '-=0.45');
      });
      gsap.utils.toArray<HTMLElement>('.l-kicker').forEach((el) => {
        const split = SplitText.create(el, { type: 'chars' });
        splits.push(split);
        gsap.from(split.chars, { autoAlpha: 0, y: 7, duration: 0.3, stagger: 0.018, ease: 'power2.out', scrollTrigger: seen(el) });
      });
      gsap.utils.toArray<HTMLElement>('[data-words]').forEach((el) => {
        const split = SplitText.create(el, { type: 'words' });
        splits.push(split);
        const words = { autoAlpha: 0, y: 12, duration: 0.6, stagger: 0.018, delay: Number(el.dataset.delay ?? 0.12), ease: 'power2.out' };
        if (heroWaiting.current && el.classList.contains('hero-sub')) heroSub.current = gsap.from(split.words, { ...words, paused: true });
        else gsap.from(split.words, { ...words, scrollTrigger: seen(el) });
      });
      gsap.utils.toArray<HTMLElement>('[data-rise]').forEach((el) => {
        gsap.from(el, { y: 30, autoAlpha: 0, duration: 0.9, delay: 0.15, clearProps: 'transform,opacity,visibility', scrollTrigger: seen(el) });
      });
      gsap.utils.toArray<HTMLElement>('.story-steps, .story-page, .try-sheet').forEach((box) => {
        gsap.from(box.querySelectorAll(':scope > li, :scope > p, :scope > h3, :scope > .try-tag'), { y: 22, autoAlpha: 0, duration: 0.75, stagger: 0.09, delay: 0.25, clearProps: 'transform,opacity,visibility', scrollTrigger: seen(box) });
      });
      gsap.from('.try-sheet, .try-panel', { y: 60, autoAlpha: 0, duration: 1, stagger: 0.12, clearProps: 'transform,opacity,visibility', scrollTrigger: seen(root.current!.querySelector('.try-grid')!) });
      gsap.from('.story-page', { x: 70, rotate: 2.5, autoAlpha: 0, duration: 1.1, clearProps: 'transform,opacity,visibility', scrollTrigger: { trigger: '.l-story', start: 'top 72%', once: true } });
      gsap.from('.teacher-note', { y: 46, rotate: 7, scale: 0.86, autoAlpha: 0, duration: 0.9, delay: 0.3, ease: 'back.out(1.7)', scrollTrigger: seen(root.current!.querySelector('.teacher-foot')!) });

      // Ambient scroll cues: the red pen progress line and the marquee.
      gsap.fromTo('.l-progress', { scaleX: 0 }, { scaleX: 1, ease: 'none', scrollTrigger: { start: 0, end: 'max', scrub: 0.3 } });
      gsap.fromTo('.l-band-track', { xPercent: 0 }, { xPercent: -28, ease: 'none', scrollTrigger: { trigger: '.l-band', start: 'top bottom', end: 'bottom top', scrub: 0.5 } });
      return () => splits.forEach((split) => split.revert());
    });

    document.fonts?.ready.then(() => ScrollTrigger.refresh());
    return () => mm.revert();
  }, { scope: root });

  return (
    <div className="landing" ref={root}>
      {showIntro && <LandingIntro onHandoff={startHero} onDone={endIntro} />}
      <a className="skip" href="#story">{t.common.skip}</a>
      <span className="l-progress" aria-hidden="true" />
      <header className="l-nav">
        <Brand to="/" />
        <nav aria-label={copy.navLabel}>
          <a href="#story" onClick={(e) => jump(e, 'story')}>{copy.navStory}</a>
          <a href="#try" onClick={(e) => jump(e, 'try')}>{copy.navTry}</a>
          <a href="#road" onClick={(e) => jump(e, 'road')}>{copy.navRoad}</a>
        </nav>
        <div className="l-right">
          <LangSwitch />
          {!STATIC_PREVIEW && (
            <Link className="l-login" to="/login" viewTransition aria-label={copy.login}>
              <span>{copy.login}</span>
              <Icon name="arrow" size={16} />
            </Link>
          )}
        </div>
      </header>

      <main>
        <section className="l-hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <h1 id="hero-title" aria-label={copy.heroLabel}>
              <span className="hero-line" aria-hidden="true"><span>{copy.hero[0]}</span></span>
              <span className="hero-line" aria-hidden="true"><span>{copy.hero[1]}</span></span>
              <span className="hero-line" aria-hidden="true"><span><span className="hero-mark-wrap"><span className="hero-mark" /><em>{copy.hero[2]}</em></span></span></span>
            </h1>
            <div className="hero-foot">
              <p className="hero-sub" data-words data-delay="0.7">{copy.heroSub}</p>
              <div className="hero-actions">
                {STATIC_PREVIEW ? (
                  <a className="l-cta" href="#try" onClick={(e) => jump(e, 'try')}>
                    <span>{copy.navTry}</span>
                    <Icon name="arrow" />
                  </a>
                ) : (
                  <>
                    <Link className="l-cta" to="/login" viewTransition>
                      <span>{copy.login}</span>
                      <Icon name="arrow" />
                    </Link>
                    <a className="l-ghost" href="#try" onClick={(e) => jump(e, 'try')}>{copy.navTry}</a>
                  </>
                )}
              </div>
            </div>
          </div>
          <div className="hero-stage" aria-hidden="true">
            <div className="hero-tilt">
              <div className="hero-sheet">
                <div className="sheet-head"><span>Passage 1</span><span>{copy.sample}</span></div>
                <p className="sheet-title">A morning at the market</p>
                <p>Every Saturday, the town square fills with small stalls. Farmers arrive before sunrise to set out vegetables, fruit and fresh bread.</p>
                <p>Most visitors come for the food, but many stay to talk. For older residents, the market is the main place to meet friends during the week.</p>
                <div className="hero-margin">
                  <svg className="hero-arrow" viewBox="0 0 64 40"><path d="M60 7C42 5 18 11 10 34M3 24l7 11 9-8" /></svg>
                  <span className="hero-note">{copy.sheetNote}</span>
                </div>
                <p>
                  <span className="sheet-circled">
                    The council moved
                    <svg className="hero-circle" viewBox="0 0 200 64" preserveAspectRatio="none"><path d="M16 36C14 16 62 5 112 6c46 1 80 12 80 29 0 18-46 24-96 24C48 59 8 52 10 30 11 20 24 13 42 10" /></svg>
                  </span>{' '}
                  the market indoors for one winter. Stallholders reported fewer customers, and it returned to the square the following spring.
                </p>
                <div className="hero-answers">
                  <span>{copy.answerSheet}</span>
                  <div>
                    {Array.from({ length: 40 }, (_, i) => (
                      <i key={i} className={i < 13 ? 'is-done' : i === 13 ? 'is-now' : undefined}>{i + 1}</i>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="l-story" id="story" aria-labelledby="story-title">
          <div className="story-side">
            <p className="l-kicker">{copy.storyKicker}</p>
            <h2 id="story-title" data-split data-accent={copy.storyAccent}>{copy.storyTitle}</h2>
            <ol className="story-steps">
              {copy.steps.map(({ title, body }, i) => (
                <li key={title} data-n={i}>
                  <span className="story-no" aria-hidden="true">0{i + 1}</span>
                  <span><strong>{title}</strong><span className="story-body">{body}</span></span>
                </li>
              ))}
            </ol>
            <span className="story-progress" aria-hidden="true"><i /></span>
          </div>
          <div className="story-stage">
            <article className="story-page">
              <span className="story-beam" aria-hidden="true" />
              <svg className="story-lens" viewBox="0 0 64 64" aria-hidden="true"><circle cx="27" cy="27" r="20" /><path d="M42 42l16 16" /></svg>
              <p className="story-question"><span>{copy.question}</span> Older residents use the market to meet friends.</p>
              <p><span className="tp">Every Saturday, the town square fills with small stalls.</span><span className="rest"> Farmers arrive before sunrise to set out vegetables, fruit and fresh bread.</span></p>
              <p>
                <span className="tp">Most visitors come for the food</span><span className="rest">, but many stay to talk. </span>
                <span className="ev">For <span className="kw">older residents</span>, the market is the main place to <span className="kw">meet friends</span> during the week.
                  <svg className="story-underline" viewBox="0 0 400 10" preserveAspectRatio="none" aria-hidden="true"><path d="M2 6c70-5 130 3 200 0s130-4 196 1" /></svg>
                </span>
              </p>
              <p><span className="tp">The council moved the market indoors for one winter.</span><span className="rest"> Stallholders reported fewer customers, and it returned to the square the following spring.</span></p>
              <span className="story-verdict">TRUE</span>
            </article>
          </div>
        </section>

        <section className="l-try" id="try" aria-labelledby="try-title">
          <div className="try-head">
            <p className="l-kicker">{copy.tryKicker}</p>
            <h2 id="try-title" data-split>{copy.tryTitle}</h2>
          </div>
          <TryIt />
        </section>

        <div className="l-band" aria-hidden="true">
          <div className="l-band-track">
            {Array.from({ length: 6 }, (_, i) => (
              <span key={i}>TRUE <b>✓</b> FALSE <b>✕</b> NOT GIVEN <b>?</b> </span>
            ))}
          </div>
        </div>

        <section className="l-teacher" aria-labelledby="teacher-title">
          <p className="l-kicker">{copy.teacherKicker}</p>
          <h2 id="teacher-title" className="teacher-line" data-split data-accent="2">{copy.teacherTitle}</h2>
          <TeacherMeme />
          <div className="teacher-foot">
            <p data-words>{copy.teacherBody}</p>
            <aside className="teacher-note" aria-label={copy.teacherNoteLabel}>
              <span>{copy.teacherNoteHead}</span>
              <p data-words data-delay="0.7">{copy.teacherNote}</p>
            </aside>
          </div>
        </section>

        <RoadMap />
      </main>

      <AuthorsFooter />
    </div>
  );
}
