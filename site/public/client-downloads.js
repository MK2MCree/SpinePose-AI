(() => {
    "use strict";

    const disclosures = [...document.querySelectorAll(".client-downloads [data-download-qr]")];
    disclosures.forEach((details) => {
        if (details.dataset.downloadQrBound) return;
        details.dataset.downloadQrBound = "true";
        const summary = details.querySelector("summary");
        if (!summary) return;

        const reflectState = () => {
            summary.setAttribute("aria-expanded", String(details.open));
            if (details.open) {
                disclosures.forEach((other) => {
                    if (other !== details) other.open = false;
                });
            }
        };

        details.addEventListener("toggle", reflectState);
        reflectState();

        document.addEventListener("pointerdown", (event) => {
            if (details.open && !details.contains(event.target)) details.open = false;
        });

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape" || !details.open) return;
            details.open = false;
            summary.focus({ preventScroll: true });
        });
    });
})();
