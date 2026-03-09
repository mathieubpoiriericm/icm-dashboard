// Dashboard Custom JavaScript

// Utility: Debounced retry - executes immediately then once after delay
// Reduced from multiple retries to minimize mid-scroll disruption
function progressiveRetry(fn, delays) {
  // Execute immediately
  fn();
  // Single delayed retry (use last delay value for stability, or default 200ms)
  var finalDelay = (delays && delays.length > 0) ? delays[delays.length - 1] : 200;
  setTimeout(fn, finalDelay);
}

// =============================================================================
// TAB CHANGE HANDLER - Explicit Bootstrap 5 tab event listener
// =============================================================================
// Using shown.bs.tab for explicit tab change handling instead of relying on
// indirect init.dt events. This provides better control over tab transitions.
$(document).on('shown.bs.tab', function(e) {
  var targetId = $(e.target).attr('data-value') || $(e.target).attr('href');

  // Reinitialize tooltips when switching tabs (elements may have been hidden)
  window.initializeTippy();

  // Sync scrollbar widths for tables in the newly visible tab
  progressiveRetry(function() {
    syncControlsWidth('firstTable');
    syncControlsWidth('secondTable');
  }, [100, 300]);

  // Invalidate Leaflet map size when Trials Map tab becomes visible
  if (targetId === 'Clinical Trials Map') {
    // Retry multiple times to handle async widget initialization
    [100, 300, 600, 1000].forEach(function(delay) {
      setTimeout(function() {
        var widget = HTMLWidgets.find('#trials_map');
        if (widget) {
          var map = widget.getMap();
          if (map) map.invalidateSize();
        }
      }, delay);
    });
  }
});

// Python plot handling
Shiny.addCustomMessageHandler('rerunPythonPlotSizing', function(msg) {
  var iframe = document.querySelector('iframe[src="python_plot.html"]');
  if (!iframe || !iframe.contentWindow) return;

  var win = iframe.contentWindow;
  if (typeof win.adjustLabelBackgrounds === 'function') win.adjustLabelBackgrounds();
  if (typeof win.adjustLegendBox === 'function') win.adjustLegendBox();
  if (typeof win.adjustLegendBoxMOA === 'function') win.adjustLegendBoxMOA();
  if (typeof win.adjustDrugLabelCollisions === 'function') win.adjustDrugLabelCollisions();
  if (typeof win.avoidCognitiveOverlap === 'function') win.avoidCognitiveOverlap();
});

// Optimized Tippy initialization with debouncing
window.tippyTimeout = null;
window.tippyInstances = [];

window.initializeTippy = function() {
  // Clear any pending initialization
  if (window.tippyTimeout) {
    clearTimeout(window.tippyTimeout);
  }

  // Debounce: wait 100ms before actually initializing
  window.tippyTimeout = setTimeout(function() {
    // Find all elements with data-tippy-content
    var elements = document.querySelectorAll('[data-tippy-content]');

    if (elements.length === 0) return;

    // Only initialize elements that don't already have Tippy
    var elementsToInit = [];
    elements.forEach(function(el) {
      if (!el._tippy) {
        elementsToInit.push(el);
      }
    });

    if (elementsToInit.length > 0) {
      var newInstances = tippy(elementsToInit, {
        allowHTML: true,
        theme: 'custom',
        placement: 'top',
        arrow: true,
        interactive: false,
        appendTo: document.body,
        maxWidth: 'none',
        trigger: 'mouseenter focus',
        onShow: function(instance) {
          var maxWidth = instance.reference.getAttribute('data-tippy-maxWidth');
          if (maxWidth) {
            var content = instance.popper.querySelector('.tippy-content');
            if (content) {
              content.style.maxWidth = maxWidth;
              content.style.whiteSpace = 'normal';
              content.style.wordWrap = 'break-word';
            }
          }
        }
      });

      // Clean up destroyed instances before adding new ones
      window.tippyInstances = window.tippyInstances.filter(
        function(inst) { return inst && !inst.state.isDestroyed; }
      ).concat(newInstances);
    }
  }, 100);
};

// =============================================================================
// UNIFIED DATATABLE EVENT HANDLERS
// =============================================================================
// Consolidated handlers for init.dt and draw.dt events to reduce overhead
// from multiple event registrations

// Single init.dt handler that performs all initialization tasks
$(document).on('init.dt', function(e, settings) {
  // 1. Initialize tooltips
  window.initializeTippy();

  // 2. Initialize top scrollbar
  initializeTopScrollbar(e, settings);

  // 3. Get table container ID for subsequent operations
  var wrapper = $(e.target).closest('.dataTables_wrapper');
  var tableContainer = wrapper.parent();
  var tableId = tableContainer.attr('id');

  // 4. Connect bslib controls if this table is registered
  if (tableId && bslibControlsRegistry[tableId]) {
    connectBslibControlsForTable(tableId, $(e.target).DataTable());
  }

  // 5. Sync control widths with progressive retry
  progressiveRetry(function() {
    syncControlsWidth(tableId);
  }, [50, 200]);

  // 6. Fix orphan DT labels (slight delay to ensure DT has finished rendering)
  setTimeout(fixOrphanDtLabels, 100);
});

// Single draw.dt handler for all redraw operations
$(document).on('draw.dt', function(e, settings) {
  // 1. Re-initialize tooltips after redraw
  window.initializeTippy();

  // 2. Update top scrollbar width
  var wrapper = $(e.target).closest('.dataTables_wrapper');
  var updateFn = wrapper.data('updateTopScrollbarWidth');
  if (typeof updateFn === 'function') {
    progressiveRetry(updateFn, [50, 200]);
  }

  // 3. Sync control widths
  var tableId = settings.sTableId;
  progressiveRetry(function() {
    syncControlsWidth(tableId);
  }, [50, 200]);
});

// Customize sample size slider with tick marks at intervals of 500
$(document).on('shiny:connected', function() {
  // Wait for the slider to be initialized (max 5 seconds = 50 attempts)
  var retryCount = 0;
  var maxRetries = 50;
  var checkSlider = setInterval(function() {
    retryCount++;
    var sliderInput = $('#sample_size_filter');
    if (sliderInput.length > 0 && sliderInput.data('ionRangeSlider')) {
      clearInterval(checkSlider);
      var slider = sliderInput.data('ionRangeSlider');
      var sliderContainer = sliderInput.closest('.irs');

      // Update slider to disable default grid
      slider.update({
        grid: false,
        prettify: function(num) {
          return num.toLocaleString();
        }
      });

      // Create custom tick marks at 500 intervals plus min/max
      var min = 15;
      var max = 3156;
      var range = max - min;
      var tickValues = [15, 500, 1000, 1500, 2000, 2500, 3000, 3156];

      // Create custom grid container
      var customGrid = $('<div class="irs-grid custom-grid" style="display: block;"></div>');

      tickValues.forEach(function(val) {
        var percent = ((val - min) / range) * 100;

        // Add tick mark
        var tick = $('<span class="irs-grid-pol"></span>');
        tick.css('left', percent + '%');
        customGrid.append(tick);

        // Add label
        var label = $('<span class="irs-grid-text"></span>');
        label.css('left', percent + '%');
        label.text(val.toLocaleString());
        customGrid.append(label);
      });

      // Append custom grid to slider
      sliderContainer.append(customGrid);
    } else if (retryCount >= maxRetries) {
      // Stop polling after max attempts to prevent memory leak
      clearInterval(checkSlider);
    }
  }, 100);
});

// =============================================================================
// TOP SCROLLBAR INITIALIZATION
// =============================================================================
// Extracted as a reusable function, called from the unified init.dt handler

function initializeTopScrollbar(e, settings) {
  var wrapper = $(e.target).closest('.dataTables_wrapper');
  var scrollBody = wrapper.find('.dataTables_scrollBody');
  var scrollContainer = wrapper.find('.dataTables_scroll');

  // Remove any existing top scrollbar to prevent duplicates
  wrapper.find('.top-scrollbar').remove();

  if (scrollBody.length > 0 && scrollContainer.length > 0) {
    var scrollBodyEl = scrollBody[0];

    // Create top scrollbar
    var topScrollbar = $('<div class="top-scrollbar"></div>');
    var topScrollbarInner = $('<div class="top-scrollbar-inner"></div>');
    topScrollbar.append(topScrollbarInner);

    // Function to sync top scrollbar width with table
    function updateTopScrollbarWidth() {
      // Match container width to scroll body width (ensures same clientWidth)
      var bodyClientWidth = scrollBodyEl.clientWidth;
      topScrollbar.css('width', bodyClientWidth + 'px');

      // Match inner width to scroll body's scrollWidth (ensures same scrollWidth)
      var scrollWidth = scrollBodyEl.scrollWidth;
      topScrollbarInner.css('width', scrollWidth + 'px');
      // This gives both scrollbars the same scroll range: scrollWidth - clientWidth
    }

    // Store the update function on the wrapper for later use
    wrapper.data('updateTopScrollbarWidth', updateTopScrollbarWidth);

    // Set initial width and retry for dynamic content
    progressiveRetry(updateTopScrollbarWidth, [100, 300, 1000]);

    // Update on window resize
    $(window).on('resize.topScrollbar', updateTopScrollbarWidth);

    // Insert top scrollbar before the scroll container
    scrollContainer.before(topScrollbar);

    var topScrollbarEl = topScrollbar[0];

    // Unified RAF-based scroll sync to prevent WebKit layout thrashing
    // Uses a single RAF and tracks which element initiated the scroll
    var syncRAF = null;
    var syncSource = null;  // 'top' or 'body'
    var lastSyncTime = 0;
    var SYNC_COOLDOWN = 16;  // ~1 frame at 60fps

    function syncScroll(source) {
      var now = performance.now();
      // Ignore if this is a programmatic scroll from the sync itself
      if (syncSource && syncSource !== source && (now - lastSyncTime) < SYNC_COOLDOWN) {
        return;
      }

      if (syncRAF) {
        cancelAnimationFrame(syncRAF);
      }

      syncSource = source;
      syncRAF = requestAnimationFrame(function() {
        lastSyncTime = performance.now();
        if (source === 'top') {
          scrollBodyEl.scrollLeft = topScrollbarEl.scrollLeft;
        } else {
          topScrollbarEl.scrollLeft = scrollBodyEl.scrollLeft;
        }
        syncRAF = null;
        // Reset source after a brief delay to allow the programmatic scroll to settle
        setTimeout(function() { syncSource = null; }, SYNC_COOLDOWN);
      });
    }

    topScrollbarEl.addEventListener('scroll', function() {
      syncScroll('top');
    }, { passive: true });

    scrollBodyEl.addEventListener('scroll', function() {
      syncScroll('body');
    }, { passive: true });

    // Scroll state management for disabling hover during scroll
    // Also RAF-debounced to prevent class toggle thrashing
    var scrollEndTimer = null;
    var scrollStateRAF = null;
    var isScrolling = false;

    function onScrollStart() {
      // Skip if we already have a pending RAF for scroll state
      if (scrollStateRAF) return;

      scrollStateRAF = requestAnimationFrame(function() {
        scrollStateRAF = null;

        if (!isScrolling) {
          isScrolling = true;
          scrollBodyEl.classList.add('is-scrolling');
        }

        clearTimeout(scrollEndTimer);
        scrollEndTimer = setTimeout(function() {
          isScrolling = false;
          scrollBodyEl.classList.remove('is-scrolling');
        }, 150);
      });
    }

    scrollBodyEl.addEventListener('scroll', onScrollStart, { passive: true });
    topScrollbarEl.addEventListener('scroll', onScrollStart, { passive: true });
  }
}

// =============================================================================
// BSLIB TABLE CONTROLS - Connect bslib inputs to DataTables
// =============================================================================

// Registry of bslib control connections (prevents duplicate init.dt listeners)
var bslibControlsRegistry = {};

// Helper function to connect bslib inputs to a DataTable
function connectBslibControlsToTable(tableId, pageLengthInputId, searchInputId) {
  // Register this table's control configuration
  bslibControlsRegistry[tableId] = {
    pageLengthInputId: pageLengthInputId,
    searchInputId: searchInputId,
    currentTable: null,
    searchTimeout: null
  };

  // Try to connect immediately if table already exists
  var existingTable = $('#' + tableId + ' table.dataTable');
  if (existingTable.length > 0 && $.fn.DataTable.isDataTable(existingTable)) {
    connectBslibControlsForTable(tableId, existingTable.DataTable());
  }
}

// Internal function to connect controls to a specific table instance
function connectBslibControlsForTable(tableId, table) {
  var config = bslibControlsRegistry[tableId];
  if (!config || !table) return;

  // Skip if already connected to this exact table instance
  if (config.currentTable === table) return;
  config.currentTable = table;

  // Connect page length select
  $('#' + config.pageLengthInputId).off('change.dtBslib').on('change.dtBslib', function() {
    var newLength = parseInt($(this).val(), 10);
    if (!isNaN(newLength) && config.currentTable) {
      config.currentTable.page.len(newLength).draw();
    }
  });

  // Connect search input with debouncing
  $('#' + config.searchInputId).off('input.dtBslib').on('input.dtBslib', function() {
    var searchVal = $(this).val();
    clearTimeout(config.searchTimeout);
    config.searchTimeout = setTimeout(function() {
      if (config.currentTable) {
        config.currentTable.search(searchVal).draw();
      }
    }, 300);
  });
}

// Connect controls for both tables when Shiny is connected
$(document).on('shiny:connected', function() {
  connectBslibControlsToTable('firstTable', 'table1_page_length', 'table1_search');
  connectBslibControlsToTable('secondTable', 'table2_page_length', 'table2_search');
});

// Sync bslib controls width with DataTables scroll width
function syncControlsWidth(tableId) {
  var tableContainer = $('#' + tableId);
  var tableWrapper = tableContainer.find('.dataTables_wrapper');
  var scrollContainer = tableWrapper.find('.dataTables_scroll');
  var controlsContainer = tableContainer.prev('.dt-bslib-controls');

  if (scrollContainer.length > 0 && controlsContainer.length > 0) {
    var scrollWidth = scrollContainer.outerWidth();
    controlsContainer.css('width', scrollWidth + 'px');
  }
}

// Sync widths on window resize (init.dt and draw.dt handled in unified handler above)
$(window).on('resize', function() {
  syncControlsWidth('firstTable');
  syncControlsWidth('secondTable');
});

// =============================================================================
// ACCESSIBILITY FIX - Convert orphan DT labels to spans
// =============================================================================

// Fix orphan DataTable "entries" labels that have no associated form field
// These generate accessibility warnings in Chrome DevTools
function fixOrphanDtLabels() {
  $('.dt-bslib-label').each(function() {
    var $label = $(this);
    // Only convert if it's actually a label element (not already fixed)
    if ($label.is('label')) {
      var $span = $('<span>').addClass('dt-bslib-label').text($label.text());
      $label.replaceWith($span);
    }
  });
}

// Run on Shiny connect (init.dt handled in unified handler above)
$(document).on('shiny:connected', fixOrphanDtLabels);


