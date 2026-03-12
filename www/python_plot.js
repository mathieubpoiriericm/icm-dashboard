
(function() {
  document.body.classList.add('svg-loading');

  const readyTimer = setTimeout(markReady, 2000);

  function markReady() {
    clearTimeout(readyTimer);
    document.body.classList.remove('svg-loading');
    document.body.classList.add('svg-ready');
  }

  function waitForFonts() {
    if (document.fonts && document.fonts.ready) {
      return document.fonts.ready.catch(() => {});
    }
    return Promise.resolve();
  }

  function nextFrame() {
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  // Safe getBBox wrapper — returns null on exception or zero-size result
  function safeGetBBox(el) {
    let bbox;
    try { bbox = el.getBBox(); } catch(e) { return null; }
    if (!bbox || bbox.width === 0) return null;
    return bbox;
  }

    // Prevent label boxes from overlapping their own drug markers
  function adjustMarkerLabelOverlap() {
    const markers = document.querySelectorAll('g.drug use');
    const svg = document.querySelector('svg');
    if (!svg) return;
    const cx = parseFloat(svg.getAttribute('width')) / 2;
    const cy = parseFloat(svg.getAttribute('height')) / 2;
    markers.forEach(mk => {
      const g = mk.closest('g.drug');
      const text = g.querySelector('text');
      const rect = g.querySelector('rect.label-bg');
      if (!text || !rect) return;

      const mkX = parseFloat(mk.getAttribute('x'));
      const mkY = parseFloat(mk.getAttribute('y'));
      const mkR = 9; // marker radius

      const rx = parseFloat(rect.getAttribute('x'));
      const ry = parseFloat(rect.getAttribute('y'));
      const rw = parseFloat(rect.getAttribute('width'));
      const rh = parseFloat(rect.getAttribute('height'));

      // Check if circle (marker) intersects rectangle (label background)
      const overlap =
        mkX + mkR > rx &&
        mkX - mkR < rx + rw &&
        mkY + mkR > ry &&
        mkY - mkR < ry + rh;

      if (overlap) {
        const bbox = safeGetBBox(text);
        if (!bbox) return;
        const dx = bbox.x - cx;
        const dy = bbox.y - cy;
        const len = Math.sqrt(dx*dx + dy*dy) || 1;

        const nudgedX = bbox.x + dx/len * 10;
        const nudgedY = bbox.y + dy/len * 10;

        text.setAttribute('x', (nudgedX + bbox.width/2).toFixed(2));
        text.setAttribute('y', (nudgedY + bbox.height/2).toFixed(2));

        const newBox = safeGetBBox(text);
        if (!newBox) return;
        const padX = 8, padY = 4;
        rect.setAttribute('x', (newBox.x - padX).toFixed(2));
        rect.setAttribute('y', (newBox.y - padY).toFixed(2));
        rect.setAttribute('width', (newBox.width + padX*2).toFixed(2));
        rect.setAttribute('height', (newBox.height + padY*2).toFixed(2));
      }
    });
  }

  function boxesOverlap(a, b) {
    return (
      a.x < b.x + b.width &&
      a.x + a.width > b.x &&
      a.y < b.y + b.height &&
      a.y + a.height > b.y
    );
  }

  function avoidCognitiveOverlap() {
    const popLabel = document.querySelector('g.pop-label[data-pop="Cognitive Impairment"]');
    const legend = document.getElementById('legend-moa-bg');
    if (!popLabel || !legend) return;

    const popBox = safeGetBBox(popLabel);
    const legBox = safeGetBBox(legend);
    if (!popBox || !legBox) return;

    if (boxesOverlap(popBox, legBox)) {
      const text = popLabel.querySelector('text');
      if (text) {
        let x = parseFloat(text.getAttribute("x"));
        x -= 30;
        text.setAttribute("x", x.toFixed(2));

        const rect = popLabel.querySelector('rect.label-bg');
        if (rect) {
          const nb = safeGetBBox(text);
          if (!nb) return;
          const padX = 8, padY = 4;
          rect.setAttribute('x', (nb.x - padX).toFixed(2));
          rect.setAttribute('y', (nb.y - padY).toFixed(2));
          rect.setAttribute('width', (nb.width + padX * 2).toFixed(2));
          rect.setAttribute('height', (nb.height + padY * 2).toFixed(2));
        }
      }
    }
  }
  // Minimal tooltip-only JS
  function hexToRgb(hex) {
    if (!hex) return null;
    let clean = hex.replace('#', '');
    if (clean.length === 3) {
      clean = clean.split('').map(ch => ch + ch).join('');
    }
    if (clean.length !== 6) return null;
    const num = parseInt(clean, 16);
    return {
      r: (num >> 16) & 255,
      g: (num >> 8) & 255,
      b: num & 255
    };
  }

  function colorChannels(color) {
    if (!color) return null;
    const trimmed = color.trim().toLowerCase();
    if (trimmed.startsWith('#')) {
      return hexToRgb(trimmed);
    }
    const match = trimmed.match(/rgba?\(([^)]+)\)/);
    if (match) {
      const parts = match[1].split(',').map(v => parseFloat(v.trim()));
      return {
        r: parts[0] ?? 0,
        g: parts[1] ?? 0,
        b: parts[2] ?? 0
      };
    }
    return null;
  }

  function toRgba(color, alpha = 1) {
    const rgb = colorChannels(color);
    if (!rgb) return `rgba(255,255,255,${alpha})`;
    return `rgba(${rgb.r},${rgb.g},${rgb.b},${alpha})`;
  }

  function isLightColor(color) {
    const rgb = colorChannels(color);
    if (!rgb) return false;
    const luminance = 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
    return luminance >= 185;
  }

  function ensureTooltip() {
    let tip = document.getElementById('tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'tooltip';
      document.body.appendChild(tip);
    }
    return tip;
  }

  // Compute clamped tooltip position: prefer above cursor, fall back to right/left
  function computeTooltipPosition(tip, x, y) {
    const tipWidth = tip.offsetWidth;
    const tipHeight = tip.offsetHeight;
    const margin = 10;
    const cursorOffset = 5;

    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let finalX = x - tipWidth / 2;
    let finalY = y - tipHeight - cursorOffset;

    const fitsAbove = finalY >= margin;

    if (fitsAbove) {
      if (finalX < margin) {
        finalX = margin;
      } else if (finalX + tipWidth > viewportWidth - margin) {
        finalX = viewportWidth - tipWidth - margin;
      }
    } else {
      // Show to the right of cursor (avoid below due to iframe clipping)
      finalX = x + cursorOffset + 15;
      finalY = y - tipHeight / 2;

      if (finalY < margin) {
        finalY = margin;
      } else if (finalY + tipHeight > viewportHeight - margin) {
        finalY = viewportHeight - tipHeight - margin;
      }

      if (finalX + tipWidth > viewportWidth - margin) {
        finalX = x - tipWidth - cursorOffset - 15;
        if (finalX < margin) {
          finalX = margin;
        }
      }
    }

    return { x: finalX, y: finalY };
  }

  function showTooltip(html, x, y, accentColor, textColor) {
    const tip = ensureTooltip();
    tip.innerHTML = html; // Content is pre-escaped via data attributes

    if (accentColor) {
      tip.style.setProperty('--tip-color-rgba', toRgba(accentColor, 0.45));
    } else {
      tip.style.setProperty('--tip-color-rgba', 'rgba(255,255,255,0.25)');
    }
    const dividerColor =
      accentColor && isLightColor(accentColor)
        ? '#000'
        : 'rgba(255,255,255,0.35)';
    tip.style.setProperty('--tip-divider-color', dividerColor);
    if (textColor) tip.style.color = textColor;

    tip.style.visibility = 'hidden';
    tip.style.display = 'flex';

    const pos = computeTooltipPosition(tip, x, y);
    tip.style.left = pos.x + 'px';
    tip.style.top = pos.y + 'px';
    tip.style.visibility = '';

    tip.classList.remove('show');
    void tip.offsetWidth;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        tip.classList.add('show');
      });
    });
  }

  function hideTooltip() {
    const tip = document.getElementById('tooltip');
    if (!tip) return;
    tip.classList.remove('show');
  }

  function updateTooltipPosition(x, y) {
    const tip = document.getElementById('tooltip');
    if (!tip) return;
    const pos = computeTooltipPosition(tip, x, y);
    tip.style.left = pos.x + 'px';
    tip.style.top = pos.y + 'px';
  }

  function readDrugAttributes(node) {
    return {
      name:    node.getAttribute('data-drug')    || 'Unknown',
      phase:   node.getAttribute('data-phase')   || '–',
      pop:     node.getAttribute('data-pop')     || '–',
      mech:    node.getAttribute('data-mech')    || '–',
      gtarget: node.getAttribute('data-gtarget') || '–',
      gevid:   node.getAttribute('data-ge')      || '–',
      tname:   node.getAttribute('data-tname')   || '–',
      regid:   node.getAttribute('data-regid')   || '–',
      svdpopd: node.getAttribute('data-svdpopd') || '–',
      ssize:   node.getAttribute('data-ssize')   || '–',
      estcomp: node.getAttribute('data-estcomp') || '–',
      pco:     node.getAttribute('data-pco')     || '–',
      sptype:  node.getAttribute('data-sptype')  || '–',
    };
  }

  function initTooltipHandlers() {
    const nodes = document.querySelectorAll('.drug');

    // Add click handlers to drug labels (text elements)
    nodes.forEach(node => {
      const textEl = node.querySelector('text');
      if (textEl) {
        textEl.addEventListener('click', (ev) => {
          ev.stopPropagation();
          // Trigger the same click handler as the marker
          node.dispatchEvent(new MouseEvent('click', {
            bubbles: false,
            cancelable: true,
            view: window
          }));
        });
      }
    });

    // Wedge tooltips
    const wedges = document.querySelectorAll('.wedge');
    wedges.forEach(w => {
      w.addEventListener('mouseover', (ev) => {
        const pop = w.getAttribute('data-pop') || '–';
        const phase = w.getAttribute('data-phase') || '–';

        const fill = w.getAttribute('fill') || '#000';
        const bg = toRgba(fill, 1);

        const html = `
  <div style="padding:2px 4px;">
    <div style="font-size:14px; font-weight:600; margin-bottom:6px; padding-bottom:6px; border-bottom:1px solid var(--tip-divider-color, #ddd);">
      ${pop}
    </div>
    <div style="font-size:12px; line-height:1.6;">
      <div><strong>Phase:</strong> ${phase}</div>
    </div>
  </div>`;
        showTooltip(html, ev.clientX, ev.clientY, bg, isLightColor(bg) ? "#000" : "#fff");

      });
      w.addEventListener('mousemove', (ev) => {
        updateTooltipPosition(ev.clientX, ev.clientY);
      });
      w.addEventListener('mouseout', () => {
        hideTooltip();
      });
    });
    nodes.forEach(node => {
      node.addEventListener('click', (ev) => {
        const d = readDrugAttributes(node);

        // Content is pre-escaped via escapeHtml() in data attributes
        const html = `
          <h2 style="margin-top:0; font-size:16px;">${d.name}</h2>
          <hr>
          <p><strong>Mechanism of Action:</strong> ${d.mech}</p>
          <p><strong>Genetic Target:</strong> ${d.gtarget}</p>
          <p><strong>Genetic Evidence:</strong> ${d.gevid}</p>
          <p><strong>Clinical Trial Name:</strong> ${d.tname}</p>
          <p><strong>Registry ID:</strong> ${d.regid}</p>
          <p><strong>Clinical Trial Phase:</strong> ${d.phase}</p>
          <p><strong>SVD Population Details:</strong> ${d.svdpopd}</p>
          <p><strong>Target Sample Size:</strong> ${d.ssize}</p>
          <p><strong>Estimated Completion Date:</strong> ${d.estcomp}</p>
          <p><strong>Primary Outcome:</strong> ${d.pco}</p>
          <p><strong>Sponsor Type:</strong> ${d.sptype}</p>
        `;

        const sb = document.getElementById('sidebar');
        const sbc = document.getElementById('sidebar-content');
        if (!sb || !sbc) return;
        sbc.innerHTML = html;
        sb.classList.add('open');

        // Wait for sidebar to render and adjust plot margin
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            const sidebarWidth = sb.offsetWidth;
            const figC = document.querySelector('.fig-c');
            if (figC) {
              figC.style.marginLeft = sidebarWidth + 'px';
              figC.style.transition = 'margin-left 0.3s ease';
            }
          });
        });
      });
      node.addEventListener('mouseover', (ev) => {
        const d = readDrugAttributes(node);

        const html = `
  <div style="padding:2px 4px;">
    <div style="font-size:14px; font-weight:600; margin-bottom:6px; padding-bottom:6px; border-bottom:1px solid var(--tip-divider-color, #ddd);">
      ${d.name}
    </div>
    <div style="font-size:12px; line-height:1.6;">
      <div><strong>Mechanism of Action:</strong> ${d.mech}</div>
      <div><strong>Genetic Target:</strong> ${d.gtarget}</div>
      <div><strong>Genetic Evidence:</strong> ${d.gevid}</div>
      <div><strong>Clinical Trial Name:</strong> ${d.tname}</div>
      <div><strong>Registry ID:</strong> ${d.regid}</div>
      <div><strong>Clinical Trial Phase:</strong> ${d.phase}</div>
      <div><strong>SVD Population Details:</strong> ${d.svdpopd}</div>
      <div><strong>Target Sample Size:</strong> ${d.ssize}</div>
      <div><strong>Estimated Completion Date:</strong> ${d.estcomp}</div>
      <div><strong>Primary Outcome:</strong> ${d.pco}</div>
      <div><strong>Sponsor Type:</strong> ${d.sptype}</div>
    </div>
  </div>`;
        const popColor = node.getAttribute('data-pop-color') || '#444444';
        showTooltip(html, ev.clientX, ev.clientY, popColor, isLightColor(popColor) ? "#000" : "#fff");
      });
      node.addEventListener('mousemove', (ev) => {
        updateTooltipPosition(ev.clientX, ev.clientY);
      });
      node.addEventListener('mouseout', () => {
        hideTooltip();
      });
    });
  }

  async function renderWhenReady() {
    try {
      await waitForFonts();
      await nextFrame();
      adjustMarkerLabelOverlap();
      avoidCognitiveOverlap();
      await nextFrame();
      setTimeout(() => {
        adjustMarkerLabelOverlap();
        avoidCognitiveOverlap();
      }, 140);
      initTooltipHandlers();
    } finally {
      markReady();
    }
  }

  window.addEventListener('load', () => {
    renderWhenReady().catch(() => markReady());
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === "Escape") {
      const sb = document.getElementById('sidebar');
      if (!sb) return;
      const figC = document.querySelector('.fig-c');
      sb.classList.remove('open');
      if (figC) {
        figC.style.marginLeft = '0';
      }
    }
  });
})();
