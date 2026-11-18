import { ChevronLeft, ChevronRight } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";

export default function PagedReader({ title, children, revision, headingId }) {
  const viewport = useRef(null);
  const [position, setPosition] = useState({ page: 0, count: 1 });

  useLayoutEffect(() => {
    const element = viewport.current;
    let active = true;
    element.scrollLeft = 0;
    const measure = () => {
      if (!active || !element.clientWidth) return;
      const count = Math.max(1, Math.round(element.scrollWidth / element.clientWidth));
      const page = Math.min(count - 1, Math.round(element.scrollLeft / element.clientWidth));
      element.scrollLeft = page * element.clientWidth;
      setPosition(old => old.page === page && old.count === count ? old : { page, count });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    document.fonts.ready.then(measure);
    return () => { active = false; observer.disconnect(); };
  }, [revision]);

  function turn(direction) {
    const page = position.page + direction;
    viewport.current.scrollLeft = page * viewport.current.clientWidth;
    setPosition({ ...position, page });
  }

  return (
    <section className="paged-reader" aria-label={title}>
      <header>
        <h2 id={headingId} tabIndex={headingId ? -1 : undefined}>{title}</h2>
        {position.count > 1 && (
          <div className="reader-pages">
            <button type="button" aria-label={`Previous page of ${title}`} disabled={!position.page} onClick={() => turn(-1)}><ChevronLeft aria-hidden="true" /></button>
            <span aria-live="polite">{position.page + 1}/{position.count}</span>
            <button type="button" aria-label={`Next page of ${title}`} disabled={position.page + 1 >= position.count} onClick={() => turn(1)}><ChevronRight aria-hidden="true" /></button>
          </div>
        )}
      </header>
      <div ref={viewport} className="reader-columns">{children}</div>
    </section>
  );
}
