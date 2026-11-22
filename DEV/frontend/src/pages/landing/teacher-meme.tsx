import { useRef, useState, type ReactNode } from 'react';
import meme from '../../assets/meme.webp';
import { useI18n } from '../../lib/i18n';
import { gsap, reducedMotion, useGSAP } from '../../lib/motion';

/** Question marks fly off the sticker, as if it cannot believe your answer. */
function burst(layer: Element) {
  for (let i = 0; i < 6; i += 1) {
    const mark = document.createElement('span');
    mark.className = 'tm-q';
    mark.textContent = '?';
    layer.appendChild(mark);
    const angle = -Math.PI * (0.06 + (0.88 * i) / 5);
    const far = 150 + (i % 3) * 36;
    gsap.set(mark, { xPercent: -50, yPercent: -50, scale: 0.3, opacity: 0 });
    gsap.to(mark, {
      keyframes: [
        { x: Math.cos(angle) * far * 0.75, y: Math.sin(angle) * far * 0.75, scale: 1.25, rotate: (i - 2.5) * 9, opacity: 1, duration: 0.38, ease: 'back.out(2)' },
        { x: Math.cos(angle) * far, y: Math.sin(angle) * far - 24, scale: 0.8, rotate: (i - 2.5) * 15, opacity: 0, duration: 0.6, ease: 'power1.in' },
      ],
      onComplete: () => mark.remove(),
    });
  }
}

export function TeacherMeme() {
  const { t } = useI18n();
  const copy = t.landing;
  const root = useRef<HTMLElement | null>(null);
  const slap = useRef<gsap.core.Timeline | null>(null);
  const [calm] = useState(reducedMotion);

  const { contextSafe } = useGSAP(() => {
    const el = root.current;
    if (!el || calm) return;
    const layer = el.querySelector('.tm-fx');
    const start = { scale: 2.3, rotate: 24, y: -70, autoAlpha: 0 };
    const stamp = { scale: 2.8, rotate: 26, autoAlpha: 0 };
    gsap.set('.tm-pose', start);
    gsap.set('.tm-stamp', stamp);

    const tl = gsap.timeline({ paused: true });
    tl.set('.tm-pose', start, 0)
      .set('.tm-stamp', stamp, 0)
      .to('.tm-pose', {
        keyframes: [
          { scale: 1, rotate: -3, y: 0, autoAlpha: 1, duration: 0.3, ease: 'power4.in' },
          { scaleX: 1.1, scaleY: 0.88, duration: 0.08, ease: 'power1.out' },
          { scaleX: 0.96, scaleY: 1.05, rotate: 2, duration: 0.14, ease: 'power1.out' },
          { scaleX: 1, scaleY: 1, rotate: 0, duration: 0.18, ease: 'power1.inOut' },
        ],
      }, 0)
      .to('.tm-stamp', { scale: 1, rotate: 0, autoAlpha: 1, duration: 0.2, ease: 'power4.in' }, 0.62)
      .to('.tm-pose', { keyframes: [{ x: -7, rotate: -2, duration: 0.05 }, { x: 6, rotate: 1.5, duration: 0.06 }, { x: -3, rotate: -0.6, duration: 0.06 }, { x: 0, rotate: 0, duration: 0.07 }] }, 0.82)
      .call(() => { if (layer) burst(layer); }, undefined, 0.84);
    slap.current = tl;

    // The slow, suspicious head tilt while it reads your answer.
    gsap.timeline({ repeat: -1, repeatDelay: 1.2, defaults: { ease: 'sine.inOut' } })
      .to('.tm-idle', { rotate: -4, y: -5, duration: 0.9 })
      .to('.tm-idle', { rotate: 3.5, y: 2, duration: 1.2 }, '+=0.5')
      .to('.tm-idle', { rotate: 0, y: 0, duration: 0.7 }, '+=0.4');

    const io = new IntersectionObserver((hits) => {
      if (hits.some((hit) => hit.isIntersecting)) {
        tl.play(0);
        io.disconnect();
      }
    }, { threshold: 0.4 });
    io.observe(el);
    return () => {
      io.disconnect();
      if (layer) {
        gsap.killTweensOf(layer.children);
        layer.replaceChildren();
      }
    };
  }, { scope: root });

  const replay = contextSafe(() => { slap.current?.play(0); });

  const body: ReactNode = (
    <>
      <span className="tm-pose">
        <span className="tm-idle"><img src={meme} alt={calm ? copy.teacherMemeAlt : ''} width={512} height={512} loading="lazy" /></span>
        <span className="tm-stamp" aria-hidden="true">{copy.teacherMemeStamp}</span>
      </span>
      <span className="tm-fx" aria-hidden="true" />
    </>
  );

  if (calm) return <span className="teacher-meme" ref={root}>{body}</span>;
  return <button type="button" className="teacher-meme" ref={(node) => { root.current = node; }} aria-label={copy.teacherMemeLabel} onClick={replay}>{body}</button>;
}
