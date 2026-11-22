import { useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { Link } from 'react-router';
import person1 from '../../assets/authors/person1.webp';
import person2 from '../../assets/authors/person2.webp';
import person3 from '../../assets/authors/person3.webp';
import { Icon } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { STATIC_PREVIEW } from '../../lib/preview';
import { gsap, reducedMotion, useGSAP } from '../../lib/motion';

const LETTERS = ['P', 'A', 'N', 'X', 'G'];
const HEART = '<path d="M12 21C5 15.5 2 12 2 8a5 5 0 0 1 10-1 5 5 0 0 1 10 1c0 4-3 7.5-10 13z"/>';

/** A real button when motion is on. With reduced motion a tap would do nothing, so it is a plain image. */
function Author({ who, src, label, calm, onTap }: { who: string; src: string; label: string; calm: boolean; onTap: () => void }) {
  const body: ReactNode = <span className="au-pose"><span className="au-idle"><img src={src} alt={calm ? label : ''} /></span></span>;
  if (calm) return <span className={`au-person ${who}`}>{body}</span>;
  return <button type="button" className={`au-person ${who}`} aria-label={label} onClick={onTap}>{body}</button>;
}

export function AuthorsFooter() {
  const { t } = useI18n();
  const copy = t.landing;
  const root = useRef<HTMLElement>(null);
  const intro = useRef<gsap.core.Timeline | null>(null);
  const [calm] = useState(reducedMotion);
  const donate = useRef<HTMLDialogElement>(null);

  const { contextSafe } = useGSAP(() => {
    const el = root.current;
    if (!el) return;

    // Person 2 hides behind the A, wherever the font puts that letter.
    const word = el.querySelector<HTMLElement>('.au-word');
    const letterA = el.querySelector<HTMLElement>('.au-letter.is-a');
    const anchor = el.querySelector<HTMLElement>('.au-anchor');
    const place = () => {
      if (!word || !letterA || !anchor) return;
      const em = parseFloat(getComputedStyle(word).fontSize);
      anchor.style.left = `${((letterA.offsetLeft + letterA.offsetWidth / 2) / em + 0.04).toFixed(4)}em`;
    };
    place();
    document.fonts?.ready.then(place);
    if (calm) return;

    const tl = gsap.timeline({ paused: true });
    el.querySelectorAll('.au-letter').forEach((letter, i) => {
      const start = { yPercent: -125, scaleX: 0.82, scaleY: 1.25, rotate: i % 2 ? 7 : -7, autoAlpha: 0 };
      gsap.set(letter, start);
      tl.set(letter, start, 0).to(letter, {
        keyframes: [
          { yPercent: 0, scaleX: 1.14, scaleY: 0.78, rotate: 0, autoAlpha: 1, duration: 0.42, ease: 'power2.in' },
          { yPercent: -9, scaleX: 0.96, scaleY: 1.07, duration: 0.14, ease: 'power1.out' },
          { yPercent: 0, scaleX: 1, scaleY: 1, duration: 0.16, ease: 'power1.inOut' },
        ],
      }, i * 0.085);
    });
    tl.fromTo('.au-p2 .au-pose', { yPercent: 102, rotate: -10 }, { yPercent: 0, rotate: 0, duration: 0.78, ease: 'back.out(1.8)' }, 0.72)
      .fromTo('.au-p1 .au-pose', { xPercent: 62, yPercent: 28, rotate: 28, scale: 0.55, autoAlpha: 0 }, { xPercent: 0, yPercent: 0, rotate: 0, scale: 1, autoAlpha: 1, duration: 0.82, ease: 'back.out(1.7)' }, 0.98)
      .fromTo('.au-p3 .au-pose', { yPercent: -18, rotate: -24, scale: 1.75, autoAlpha: 0 }, { yPercent: 0, rotate: 0, scale: 1, autoAlpha: 1, duration: 0.76, ease: 'elastic.out(1, 0.6)' }, 1.32);
    // Each person lands first, then speaks.
    ([['.au-b2', 1.42], ['.au-b1', 1.76], ['.au-b3', 2.02]] as const).forEach(([bubble, at]) => {
      tl.fromTo(bubble, { scale: 0.35, y: 10, autoAlpha: 0 }, { scale: 1, y: 0, autoAlpha: 1, duration: 0.42, ease: 'back.out(2.6)' }, at);
    });
    intro.current = tl;

    gsap.fromTo('.au-p3 .au-idle', { rotate: -2 }, { rotate: 2.4, duration: 2.3, repeat: -1, yoyo: true, ease: 'sine.inOut' });
    gsap.fromTo('.au-p1 .au-idle', { rotate: -1.6 }, { rotate: 1.8, yPercent: -1.2, duration: 1.7, repeat: -1, yoyo: true, ease: 'sine.inOut' });
    gsap.timeline({ repeat: -1, repeatDelay: 3.5 })
      .to('.au-p2 .au-idle', { yPercent: 16, rotate: -3, duration: 0.5, ease: 'sine.inOut' })
      .to('.au-p2 .au-idle', { yPercent: -4, rotate: 3, duration: 0.45, ease: 'sine.out' }, '+=0.3')
      .to('.au-p2 .au-idle', { yPercent: 0, rotate: 0, duration: 0.35, ease: 'sine.inOut' });

    const io = new IntersectionObserver((hits) => {
      if (hits.some((hit) => hit.isIntersecting)) {
        tl.play(0);
        io.disconnect();
      }
    }, { threshold: 0.3 });
    io.observe(el);

    const fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    if (!fine) return () => io.disconnect();
    const drift = [
      ['.au-p1', 16, 7],
      ['.au-p2', 12, 5],
      ['.au-p3', -14, -8],
    ].map(([sel, x, y]) => ({
      x: gsap.quickTo(sel as string, 'x', { duration: 0.6, ease: 'power3' }),
      y: gsap.quickTo(sel as string, 'y', { duration: 0.6, ease: 'power3' }),
      dx: x as number,
      dy: y as number,
    }));
    const onMove = (e: PointerEvent) => {
      const box = el.getBoundingClientRect();
      const px = (e.clientX - box.left) / box.width - 0.5;
      const py = (e.clientY - box.top) / box.height - 0.5;
      drift.forEach((d) => { d.x(px * d.dx); d.y(py * d.dy); });
    };
    el.addEventListener('pointermove', onMove);
    return () => {
      io.disconnect();
      el.removeEventListener('pointermove', onMove);
    };
  }, { scope: root });

  const pop = contextSafe((selector: string) => {
    if (intro.current?.isActive()) return; // A tap must not cancel a bubble that is still arriving.
    gsap.to(selector, { keyframes: [{ scale: 1.09, y: -3, duration: 0.11 }, { scale: 1, y: 0, duration: 0.17 }], overwrite: true });
  });

  const boing = contextSafe((selector: string) => {
    gsap.to(`${selector} .au-pose`, {
      keyframes: [
        { scaleX: 1.1, scaleY: 0.88, duration: 0.11 },
        { scaleX: 0.94, scaleY: 1.1, duration: 0.14 },
        { scaleX: 1.02, scaleY: 0.98, duration: 0.11 },
        { scaleX: 1, scaleY: 1, duration: 0.1 },
      ],
      overwrite: 'auto',
    });
  });

  const hearts = contextSafe(() => {
    const el = root.current;
    const peek = el?.querySelector('.au-peek');
    const layer = el?.querySelector('.au-fx');
    if (!el || !peek || !layer) return;
    const box = el.getBoundingClientRect();
    const face = peek.getBoundingClientRect();
    for (let i = 0; i < 7; i += 1) {
      const heart = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      heart.setAttribute('viewBox', '0 0 24 24');
      heart.setAttribute('class', 'au-heart');
      heart.innerHTML = HEART;
      heart.style.left = `${face.left - box.left + face.width / 2}px`;
      heart.style.top = `${face.top - box.top + face.height * 0.32}px`;
      layer.appendChild(heart);
      const angle = -Math.PI * (0.12 + (0.76 * i) / 6);
      const far = 95 + (i % 3) * 34;
      gsap.set(heart, { x: 0, y: 0, scale: 0.3, opacity: 0 });
      gsap.to(heart, {
        keyframes: [
          { x: Math.cos(angle) * far * 0.7, y: Math.sin(angle) * far * 0.7, scale: 1.2, rotate: (i - 3) * 6, opacity: 1, duration: 0.4 },
          { x: Math.cos(angle) * far, y: Math.sin(angle) * far - 28, scale: 0.8, rotate: (i - 3) * 11, opacity: 0, duration: 0.58 + i * 0.035 },
        ],
        onComplete: () => heart.remove(),
      });
    }
  });

  const openDonate = contextSafe(() => {
    donate.current?.showModal();
    if (calm) return;
    gsap.fromTo('.au-donate', { scale: 0.55, rotate: -7, y: 40, autoAlpha: 0 }, { scale: 1, rotate: 0, y: 0, autoAlpha: 1, duration: 0.55, ease: 'back.out(2)' });
    gsap.fromTo('.au-donate img', { yPercent: 70, rotate: -18 }, { yPercent: 0, rotate: 0, duration: 0.8, delay: 0.14, ease: 'elastic.out(1, 0.55)' });
    gsap.fromTo('.au-donate h3, .au-donate p, .au-donate button', { y: 16, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.45, stagger: 0.08, delay: 0.22, ease: 'power3.out' });
  });
  const onDonateClick = (e: MouseEvent<HTMLDialogElement>) => {
    if (e.target === donate.current) donate.current?.close();
  };

  const sayHi = () => { boing('.au-p2'); pop('.au-b2'); hearts(); };
  const snap = () => { boing('.au-p1'); pop('.au-b1'); };
  const poke = contextSafe(() => {
    pop('.au-b3');
    gsap.to('.au-p3 .au-pose', {
      keyframes: [{ rotate: -11, scale: 1.06, duration: 0.14 }, { rotate: 9, scale: 1, duration: 0.16 }, { rotate: -4, duration: 0.16 }, { rotate: 0, duration: 0.16 }],
      overwrite: 'auto',
    });
  });

  return (
    <footer className="au" ref={root}>
      <div className="au-in">
        <h2 data-split>{copy.footTitle[0]}<br />{copy.footTitle[1]}</h2>
        <p data-words>{copy.footSub}</p>
        {!STATIC_PREVIEW && (
          <Link className="l-cta au-cta" to="/login" viewTransition data-rise>
            <span>{copy.login}</span>
            <Icon name="arrow" />
          </Link>
        )}
        <Author who="au-p3" src={person3} label={copy.authorStudyLabel} calm={calm} onTap={poke} />
        <div className="au-bubble au-b3">{copy.authorStudy}</div>
        <div className="au-stage">
          <span className="sr-only">PANXG</span>
          <div className="au-word">
            <div className="au-anchor">
              <div className="au-peek"><Author who="au-p2" src={person2} label={copy.authorHiLabel} calm={calm} onTap={sayHi} /></div>
              <div className="au-bubble au-b2">{copy.authorHi}</div>
            </div>
            {LETTERS.map((ch, i) => (
              <span key={ch} className={`au-letter${i === 1 ? ' is-a' : ''}${i === 1 || i === 3 ? ' is-red' : ''}`} aria-hidden="true">{ch}</span>
            ))}
            <Author who="au-p1" src={person1} label={copy.authorCheeseLabel} calm={calm} onTap={snap} />
          </div>
          <div className="au-bubble au-b1">{copy.authorCheese}</div>
        </div>
        <div className="au-meta">
          <span>© 2026 PANXG English</span>
          <span className="au-tools">
            <button type="button" className="au-donate-btn" onClick={openDonate}>
              <svg viewBox="0 0 24 24" aria-hidden="true" dangerouslySetInnerHTML={{ __html: HEART }} />
              {copy.footDonate}
            </button>
            {!calm && <button type="button" className="au-replay" onClick={() => intro.current?.play(0)}>{copy.footReplay}</button>}
          </span>
        </div>
      </div>
      <div className="au-fx" aria-hidden="true" />
      <dialog className="au-donate" ref={donate} aria-labelledby="donate-title" onClick={onDonateClick}>
        <div className="au-donate-in">
          <img src={person2} alt="" />
          <h3 id="donate-title">{copy.donateTitle}</h3>
          <p>{copy.donateBody}</p>
          <button type="button" onClick={() => donate.current?.close()}>{copy.donateClose}</button>
        </div>
      </dialog>
    </footer>
  );
}
