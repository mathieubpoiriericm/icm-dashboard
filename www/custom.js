// Dashboard Custom JavaScript

// Utility: Progressive retry with exponential backoff (consolidates scattered setTimeout chains)
function progressiveRetry(fn, delays) {
  delays = delays || [50, 150, 400];
  fn(); // Execute immediately
  delays.forEach(function(delay) {
    setTimeout(fn, delay);
  });
}

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

      // Store instances for potential cleanup later
      window.tippyInstances = window.tippyInstances.concat(newInstances);
    }
  }, 100);
};

// Initialize on DataTable events (reduced timeouts)
$(document).on('init.dt draw.dt', function(e, settings) {
  window.initializeTippy();
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

// Scroll state management for DataTables (disables hover during scroll for performance)
// Initialize top scrollbar on table init
$(document).on('init.dt', function(e, settings) {
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
    var isSyncing = false;

    // Sync top scrollbar -> table scroll
    topScrollbarEl.addEventListener('scroll', function() {
      if (!isSyncing) {
        isSyncing = true;
        scrollBodyEl.scrollLeft = topScrollbarEl.scrollLeft;
        isSyncing = false;
      }
    }, { passive: true });

    // Sync table scroll -> top scrollbar
    scrollBodyEl.addEventListener('scroll', function() {
      if (!isSyncing) {
        isSyncing = true;
        topScrollbarEl.scrollLeft = scrollBodyEl.scrollLeft;
        isSyncing = false;
      }
    }, { passive: true });

    // Scroll state management for disabling hover during scroll
    var scrollEndTimer = null;

    function onScrollStart() {
      if (!scrollBodyEl.classList.contains('is-scrolling')) {
        scrollBodyEl.classList.add('is-scrolling');
      }
      clearTimeout(scrollEndTimer);
      scrollEndTimer = setTimeout(function() {
        scrollBodyEl.classList.remove('is-scrolling');
      }, 150);
    }

    scrollBodyEl.addEventListener('scroll', onScrollStart, { passive: true });
    topScrollbarEl.addEventListener('scroll', onScrollStart, { passive: true });
  }
});

// Update top scrollbar width on table redraw (pagination, filtering, sorting)
$(document).on('draw.dt', function(e, settings) {
  var wrapper = $(e.target).closest('.dataTables_wrapper');
  var updateFn = wrapper.data('updateTopScrollbarWidth');
  if (typeof updateFn === 'function') {
    // Update with progressive retry for dynamic content
    progressiveRetry(updateFn, [50, 200]);
  }
});

// =============================================================================
// BSLIB TABLE CONTROLS - Connect bslib inputs to DataTables
// =============================================================================

// Helper function to connect bslib inputs to a DataTable
function connectBslibControlsToTable(tableId, pageLengthInputId, searchInputId) {
  var currentTable = null;
  var searchTimeout = null;

  function connectControls(table) {
    if (!table) return;

    // Skip if already connected to this exact table instance
    if (currentTable === table) return;
    currentTable = table;

    // Connect page length select
    $('#' + pageLengthInputId).off('change.dtBslib').on('change.dtBslib', function() {
      var newLength = parseInt($(this).val(), 10);
      if (!isNaN(newLength) && currentTable) {
        currentTable.page.len(newLength).draw();
      }
    });

    // Connect search input with debouncing
    $('#' + searchInputId).off('input.dtBslib').on('input.dtBslib', function() {
      var searchVal = $(this).val();
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(function() {
        if (currentTable) {
          currentTable.search(searchVal).draw();
        }
      }, 300);
    });
  }

  // Try to connect immediately if table already exists
  var existingTable = $('#' + tableId + ' table.dataTable');
  if (existingTable.length > 0 && $.fn.DataTable.isDataTable(existingTable)) {
    connectControls(existingTable.DataTable());
  }

  // Also listen for init.dt in case table loads later or is reinitialized (tab switch)
  $(document).on('init.dt', function(e, settings) {
    // Check if the initialized table is inside our container
    var tableContainer = $(e.target).closest('#' + tableId);
    if (tableContainer.length > 0) {
      connectControls($(e.target).DataTable());
    }
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

// Sync widths on table init and draw
$(document).on('init.dt draw.dt', function(e, settings) {
  var tableId = settings.sTableId;
  // Progressive retry to ensure table is fully rendered
  progressiveRetry(function() {
    syncControlsWidth(tableId);
  }, [50, 200]);
});

// Sync widths on window resize
$(window).on('resize', function() {
  syncControlsWidth('firstTable');
  syncControlsWidth('secondTable');
});
