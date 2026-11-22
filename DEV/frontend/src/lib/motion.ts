import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { useLayoutEffect, useRef } from 'react';

gsap.registerPlugin(useGSAP);
gsap.defaults({ ease: 'expo.out', duration: 0.7 });

export { gsap, useGSAP };

export const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Staggers in children that carry a `data-key` the list has not shown before.
 * Existing rows stay still when data refetches, so only real additions move.
 */
export function useFreshItems<T extends HTMLElement>(signature: string) {
  const ref = useRef<T>(null);
  const seen = useRef(new Set<string>());

  useLayoutEffect(() => {
    const items = Array.from(ref.current?.children ?? []) as HTMLElement[];
    const fresh = items.filter((el) => el.dataset.key && !seen.current.has(el.dataset.key));
    if (fresh.length === 0) return;
    fresh.forEach((el) => seen.current.add(el.dataset.key!));
    if (reducedMotion()) return;

    const tween = gsap.fromTo(
      fresh,
      { autoAlpha: 0, y: 18 },
      { autoAlpha: 1, y: 0, duration: 0.7, stagger: 0.055, clearProps: 'opacity,visibility,transform' },
    );
    return () => {
      if (tween.progress() < 1) {
        fresh.forEach((el) => seen.current.delete(el.dataset.key!));
        tween.revert();
      }
    };
  }, [signature]);

  return ref;
}
