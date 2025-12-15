
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

    // Prevent label boxes from overlapping their own drug markers
  function adjustMarkerLabelOverlap() {
    const markers = document.querySelectorAll('g.drug use');
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
        // Nudge label outward radially
        const svg = document.querySelector('svg');
        const cx = parseFloat(svg.getAttribute('width')) / 2;
        const cy = parseFloat(svg.getAttribute('height')) / 2;

        const bbox = text.getBBox();
        const dx = bbox.x - cx;
        const dy = bbox.y - cy;
        const len = Math.sqrt(dx*dx + dy*dy) || 1;

        const nudgedX = bbox.x + dx/len * 10;
        const nudgedY = bbox.y + dy/len * 10;

        text.setAttribute('x', (nudgedX + bbox.width/2).toFixed(2));
        text.setAttribute('y', (nudgedY + bbox.height/2).toFixed(2));

        const newBox = text.getBBox();
        const padX = 8, padY = 4;
        rect.setAttribute('x', (newBox.x - padX).toFixed(2));
        rect.setAttribute('y', (newBox.y - padY).toFixed(2));
        rect.setAttribute('width', (newBox.width + padX*2).toFixed(2));
        rect.setAttribute('height', (newBox.height + padY*2).toFixed(2));
      }
    });
  }

  // --- Inserted functions for Cognitive Impairment overlap ---
  function boxesOverlap(a, b) {
    return (
      a.x < b.x + b.width &&
      a.x + a.width > b.x &&
      a.y < b.y + b.height &&
      a.y + a.height > b.y
    );
  }

  // Fix two-line population label boxes - DISABLED due to Chromium getBBox() bug in iframes
  // Rects are pre-rendered with correct dimensions
  function adjustTwoLinePopLabels() {
    return; // Disabled - getBBox() returns incorrect values in Chromium iframes
  }

  function avoidCognitiveOverlap() {
    const popLabel = document.querySelector('g.pop-label[data-pop="Cognitive Impairment"]');
    const legend = document.getElementById('legend-moa-bg');
    if (!popLabel || !legend) return;

    const popBox = popLabel.getBBox();
    const legBox = legend.getBBox();

    if (boxesOverlap(popBox, legBox)) {
      const text = popLabel.querySelector('text');
      if (text) {
        let x = parseFloat(text.getAttribute("x"));
        x -= 30;
        text.setAttribute("x", x.toFixed(2));

        const rect = popLabel.querySelector('rect.label-bg');
        if (rect) {
          const nb = text.getBBox();
          const padX = 8, padY = 4;
          rect.setAttribute('x', (nb.x - padX).toFixed(2));
          rect.setAttribute('y', (nb.y - padY).toFixed(2));
          rect.setAttribute('width', (nb.width + padX * 2).toFixed(2));
          rect.setAttribute('height', (nb.height + padY * 2).toFixed(2));
        }
      }
    }
  }
  // --- End inserted functions ---

  // Fine-tune label background rectangles (boxes are pre-rendered with estimated dimensions)
  function adjustLabelBackgrounds() {
    // DISABLED: Boxes are fully pre-rendered with proper dimensions
    // This function is intentionally disabled to avoid Chromium getBBox() bugs in iframes
    // If you need fine-tuning, uncomment the code below, but the pre-rendered dimensions
    // should be accurate enough for production use.
    return;

    /* OPTIONAL FINE-TUNING (disabled to avoid Chromium bugs):
    const groups = document.querySelectorAll('g.pop-label, g.phase-label, g.drug');
    groups.forEach(g => {
      const rect = g.querySelector('rect.label-bg');
      const text = g.querySelector('text');
      if (!rect || !text) return;

      try {
        const bbox = text.getBBox();
        const padX = 8;
        const padY = 4;
        rect.setAttribute('x', (bbox.x - padX).toFixed(2));
        rect.setAttribute('y', (bbox.y - padY).toFixed(2));
        rect.setAttribute('width', (bbox.width + padX * 2).toFixed(2));
        rect.setAttribute('height', (bbox.height + padY * 2).toFixed(2));
      } catch (e) {
        console.warn('getBBox failed for label, keeping pre-rendered dimensions:', e);
      }
    });
    */
  }

  // Auto-size legend box (kept for fine-tuning, but legends are pre-rendered)
  function adjustLegendBox() {
      // Legend boxes are pre-rendered with proper dimensions
      // This function kept for compatibility but does minimal work
      const bg = document.getElementById('legend-bg');
      if (!bg) return;

      // Check if already has proper dimensions
      const currentWidth = parseFloat(bg.getAttribute('width'));
      if (currentWidth > 0) return; // Already properly sized, skip adjustment
  }

  // Auto-size MOA legend box (kept for fine-tuning, but legend is pre-rendered)
  function adjustLegendBoxMOA() {
    // MOA legend box is pre-rendered with proper dimensions
    // This function kept for compatibility but does minimal work
    const bg = document.getElementById('legend-moa-bg');
    if (!bg) return;

    // Check if already has proper dimensions
    const currentWidth = parseFloat(bg.getAttribute('width'));
    if (currentWidth > 0) return; // Already properly sized, skip adjustment
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

  function showTooltip(html, x, y, accentColor) {
    const tip = ensureTooltip();
    tip.innerHTML = html;

    // Set color properties
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

    // Temporarily show to measure dimensions
    tip.style.visibility = 'hidden';
    tip.style.display = 'flex';

    const tipWidth = tip.offsetWidth;
    const tipHeight = tip.offsetHeight;
    const margin = 10; // margin from viewport edges
    const cursorOffset = 5; // offset from cursor

    // Get viewport dimensions
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    // Calculate initial position (centered above cursor)
    let finalX = x - tipWidth / 2;
    let finalY = y - tipHeight - cursorOffset;

    // Check if tooltip fits above cursor
    const fitsAbove = finalY >= margin;

    if (fitsAbove) {
      // Tooltip fits above - adjust horizontal position to stay within viewport
      if (finalX < margin) {
        finalX = margin;
      } else if (finalX + tipWidth > viewportWidth - margin) {
        finalX = viewportWidth - tipWidth - margin;
      }
    } else {
      // Doesn't fit above - show to the right of cursor (avoid below due to
      // iframe clipping issues)
      finalX = x + cursorOffset + 15;
      finalY = y - tipHeight / 2; // vertically centered on cursor

      // Clamp vertical position
      if (finalY < margin) {
        finalY = margin;
      } else if (finalY + tipHeight > viewportHeight - margin) {
        finalY = viewportHeight - tipHeight - margin;
      }

      // If right side doesn't fit, try left side
      if (finalX + tipWidth > viewportWidth - margin) {
        finalX = x - tipWidth - cursorOffset - 15;
        if (finalX < margin) {
          finalX = margin;
        }
      }
    }

    tip.style.left = finalX + 'px';
    tip.style.top = finalY + 'px';
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

    const tipWidth = tip.offsetWidth;
    const tipHeight = tip.offsetHeight;
    const margin = 10;
    const cursorOffset = 5;

    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    // Calculate initial position (centered above cursor)
    let finalX = x - tipWidth / 2;
    let finalY = y - tipHeight - cursorOffset;

    // Check if tooltip fits above cursor
    const fitsAbove = finalY >= margin;

    if (fitsAbove) {
      // Tooltip fits above - adjust horizontal position to stay within viewport
      if (finalX < margin) {
        finalX = margin;
      } else if (finalX + tipWidth > viewportWidth - margin) {
        finalX = viewportWidth - tipWidth - margin;
      }
    } else {
      // Doesn't fit above - show to the right of cursor (avoid below due to
      // iframe clipping issues)
      finalX = x + cursorOffset + 15;
      finalY = y - tipHeight / 2; // vertically centered on cursor

      // Clamp vertical position
      if (finalY < margin) {
        finalY = margin;
      } else if (finalY + tipHeight > viewportHeight - margin) {
        finalY = viewportHeight - tipHeight - margin;
      }

      // If right side doesn't fit, try left side
      if (finalX + tipWidth > viewportWidth - margin) {
        finalX = x - tipWidth - cursorOffset - 15;
        if (finalX < margin) {
          finalX = margin;
        }
      }
    }

    tip.style.left = finalX + 'px';
    tip.style.top = finalY + 'px';
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

        // Read exact wedge fill & opacity from the SVG element
        const fill = w.getAttribute('fill') || '#000';
        const op = parseFloat(w.getAttribute('fill-opacity') || '1');

        // Use wedge opacity instead of a fixed tooltipOpacity
        let bg = fill;
        if (fill.startsWith('#') && (fill.length === 7 || fill.length === 4)) {
          const hex = fill.length === 7 ? fill.slice(1) : (
            fill[1] + fill[1] + fill[2] + fill[2] + fill[3] + fill[3]
          );
          const r = parseInt(hex.slice(0,2),16);
          const g = parseInt(hex.slice(2,4),16);
          const b = parseInt(hex.slice(4,6),16);
          bg = `rgba(${r},${g},${b},1)`;
        }

        const html = `
  <div style="padding:2px 4px;">
    <div style="font-size:14px; font-weight:600; margin-bottom:6px; padding-bottom:6px; border-bottom:1px solid var(--tip-divider-color, #ddd);">
      ${pop}
    </div>
    <div style="font-size:12px; line-height:1.6;">
      <div><strong>Phase:</strong> ${phase}</div>
    </div>
  </div>`;
        const tip = ensureTooltip();
        tip.style.color = isLightColor(bg) ? "#000" : "#fff";

        showTooltip(html, ev.clientX, ev.clientY, bg);

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
        const name    = node.getAttribute('data-drug')    || 'Unknown';
        const phase   = node.getAttribute('data-phase')    || '–';
        const pop     = node.getAttribute('data-pop')      || '–';
        const mech    = node.getAttribute('data-mech')     || '–';
        const gtarget = node.getAttribute('data-gtarget')  || '–';
        const gevid   = node.getAttribute('data-ge')       || '–';
        const tname   = node.getAttribute('data-tname')    || '–';
        const regid   = node.getAttribute('data-regid')    || '–';
        const svdpopd = node.getAttribute('data-svdpopd')  || '–';
        const ssize   = node.getAttribute('data-ssize')    || '–';
        const estcomp = node.getAttribute('data-estcomp')  || '–';
        const pco     = node.getAttribute('data-pco')      || '–';
        const sptype  = node.getAttribute('data-sptype')   || '–';

        const html = `
          <h2 style="margin-top:0; font-size:16px;">${name}</h2>
          <hr>
          <p><strong>Mechanism of Action:</strong> ${mech}</p>
          <p><strong>Genetic Target:</strong> ${gtarget}</p>
          <p><strong>Genetic Evidence:</strong> ${gevid}</p>
          <p><strong>Clinical Trial Name:</strong> ${tname}</p>
          <p><strong>Registry ID:</strong> ${regid}</p>
          <p><strong>Clinical Trial Phase:</strong> ${phase}</p>
          <p><strong>SVD Population Details:</strong> ${svdpopd}</p>
          <p><strong>Target Sample Size:</strong> ${ssize}</p>
          <p><strong>Estimated Completion Date:</strong> ${estcomp}</p>
          <p><strong>Primary Outcome:</strong> ${pco}</p>
          <p><strong>Sponsor Type:</strong> ${sptype}</p>
        `;

        const sb = document.getElementById('sidebar');
        const sbc = document.getElementById('sidebar-content');
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
        const name    = node.getAttribute('data-drug')    || 'Unknown';
        const phase   = node.getAttribute('data-phase')    || '–';
        const pop     = node.getAttribute('data-pop')      || '–';
        const mech    = node.getAttribute('data-mech')     || '–';
        const gtarget = node.getAttribute('data-gtarget')  || '–';
        const gevid   = node.getAttribute('data-ge')       || '–';
        const tname   = node.getAttribute('data-tname')    || '–';
        const regid   = node.getAttribute('data-regid')    || '–';
        const svdpopd = node.getAttribute('data-svdpopd')  || '–';
        const ssize   = node.getAttribute('data-ssize')    || '–';
        const estcomp = node.getAttribute('data-estcomp')  || '–';
        const pco     = node.getAttribute('data-pco')      || '–';
        const sptype  = node.getAttribute('data-sptype')   || '–';

        const html = `
  <div style="padding:2px 4px;">
    <div style="font-size:14px; font-weight:600; margin-bottom:6px; padding-bottom:6px; border-bottom:1px solid var(--tip-divider-color, #ddd);">
      ${name}
    </div>
    <div style="font-size:12px; line-height:1.6;">
      <div><strong>Mechanism of Action:</strong> ${mech}</div>
      <div><strong>Genetic Target:</strong> ${gtarget}</div>
      <div><strong>Genetic Evidence:</strong> ${gevid}</div>
      <div><strong>Clinical Trial Name:</strong> ${tname}</div>
      <div><strong>Registry ID:</strong> ${regid}</div>
      <div><strong>Clinical Trial Phase:</strong> ${phase}</div>
      <div><strong>SVD Population Details:</strong> ${svdpopd}</div>
      <div><strong>Target Sample Size:</strong> ${ssize}</div>
      <div><strong>Estimated Completion Date:</strong> ${estcomp}</div>
      <div><strong>Primary Outcome:</strong> ${pco}</div>
      <div><strong>Sponsor Type:</strong> ${sptype}</div>
    </div>
  </div>`;
        const popColor = node.getAttribute('data-pop-color') || '#444444';
        const tip = ensureTooltip();
        tip.style.color = isLightColor(popColor) ? "#000" : "#fff";
        showTooltip(html, ev.clientX, ev.clientY, popColor);
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
      adjustTwoLinePopLabels();
      adjustLabelBackgrounds();
      adjustMarkerLabelOverlap();
      adjustLegendBox();
      adjustLegendBoxMOA();
      if (typeof adjustDrugLabelCollisions === 'function') {
        adjustDrugLabelCollisions();
      }
      avoidCognitiveOverlap();
      await nextFrame();
      setTimeout(() => {
        adjustTwoLinePopLabels();
        adjustLabelBackgrounds();
        adjustMarkerLabelOverlap();
        adjustLegendBox();
        adjustLegendBoxMOA();
        if (typeof adjustDrugLabelCollisions === 'function') {
          adjustDrugLabelCollisions();
        }
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
      const figC = document.querySelector('.fig-c');
      sb.classList.remove('open');
      if (figC) {
        figC.style.marginLeft = '0';
      }
    }
  });
})();
