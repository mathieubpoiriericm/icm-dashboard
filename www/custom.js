// Dashboard Custom JavaScript

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
    console.log('Initializing Tippy...');

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
      console.log('Initializing', elementsToInit.length, 'new Tippy instances');

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
$(document).on('init.dt', function(e, settings) {
  console.log('DataTable initialized');
  window.initializeTippy();
});

$(document).on('draw.dt', function(e, settings) {
  console.log('DataTable drawn');
  window.initializeTippy();
});

// Customize sample size slider with tick marks at intervals of 500
$(document).on('shiny:connected', function() {
  // Wait for the slider to be initialized
  var checkSlider = setInterval(function() {
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
    }
  }, 100);
});

// Scroll state management for DataTables (disables hover during scroll for performance)
$(document).on('init.dt draw.dt', function(e, settings) {
  var wrapper = $(e.target).closest('.dataTables_wrapper');
  var scrollBody = wrapper.find('.dataTables_scrollBody');

  // Remove any existing top scrollbar to prevent WebKit screen tearing
  wrapper.find('.top-scrollbar').remove();

  if (scrollBody.length > 0) {
    var scrollBodyEl = scrollBody[0];

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
  }
});
