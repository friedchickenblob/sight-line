// Light/dark override. With no override the page follows the system theme (see style.css).
// The SightLine control page (phone-alert/server) shows this dashboard in a frame and sets the theme:
//   - at load, with ?theme=light|dark on the URL
//   - later, with window.postMessage({type: "sightline-theme", value: "light" | "dark" | "auto"}, "*")
// Only the embedding page (window.parent) is listened to.
(function () {
  var root = document.documentElement;
  function set(value) {
    if (value === "light" || value === "dark") root.setAttribute("data-theme", value);
    else root.removeAttribute("data-theme");
  }
  var m = /[?&]theme=(light|dark)/.exec(location.search);
  if (m) set(m[1]);
  window.addEventListener("message", function (e) {
    if (e.source === window.parent && e.data && e.data.type === "sightline-theme") set(e.data.value);
  });
})();
