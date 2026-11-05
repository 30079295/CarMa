
 // Get the vehicle ID from HTML
const vehicleId = document.body.dataset.vehicleId;

// Find all four maintenance checkboxes
const checkboxes = document.querySelectorAll(".maintenance-checkbox");

// Go through each checkbox
checkboxes.forEach((checkbox) => {

    // Detect when the checkbox is checked or unchecked
    checkbox.addEventListener("change", () => {

        // Get the Supabase column linked to the checkbox
        const field = checkbox.dataset.field;

        // Check if the checkbox is ticked or not
        const checked = checkbox.checked;

        // Send the checkbox update to Flask (app.py)
        fetch(`/vehicle/${vehicleId}/maintenance-check`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },

            // Convert the checkbox data into JSON for Supabase
            body: JSON.stringify({
                field: field,
                checked: checked
            })
        })
        .then((response) => {
            if (!response.ok) {
                throw new Error("Checkbox failed to save");
            }
        })

        // Handle an error if the checkbox could not be saved
        .catch((error) => {
            checkbox.checked = !checked;
            alert("Unable to save maintenance check. Please try again.");
            console.error(error);
        });

    });

});

// Save service details without leaving the page
const serviceForm = document.getElementById("serviceDetailsForm");
const serviceError = document.getElementById("serviceError");

// Clear errors when the popup is closed
document.getElementById("updateServiceModal").addEventListener("hidden.bs.modal", function () {
    serviceError.hidden = true;
    serviceError.textContent = "";
});

serviceForm.addEventListener("submit", async function (event) {
    event.preventDefault();

    serviceError.hidden = true;

    const formData = new FormData(serviceForm);
    const saveButton = serviceForm.querySelector('button[type="submit"]');

    saveButton.disabled = true;

    try {
        const response = await fetch(serviceForm.action, {
            method: "POST",
            body: formData
        });

        // Show Flask validation errors on the service page
        if (response.redirected && response.url.includes("error=")) {
            window.location.href = response.url;
            return;
        }

        if (!response.ok) {
            const message = await response.text();
            serviceError.textContent = message;
            serviceError.hidden = false;
            return;
        }

        // Redirect to login if the session expired
        if (response.redirected && response.url.includes("/login")) {
            window.location.href = response.url;
            return;
        }

        // Refresh to show updated service details
        window.location.reload();

    } catch (error) {
        serviceError.textContent = "Unable to save service details. Please try again.";
        serviceError.hidden = false;

    } finally {
        saveButton.disabled = false;
    }
});

// Reopen the popup if Flask returned a service error
const errorType = new URLSearchParams(window.location.search).get("error");

if (["service-invalid", "interval-zero", "service-higher", "service-maximum", "interval-maximum", "no-service-invoice"].includes(errorType)) {
    const serviceModal = new bootstrap.Modal(document.getElementById("updateServiceModal"));
    serviceModal.show();
}

//Set the service progress bar width
const serviceProgressBar = document.querySelector(".service-progress .progress-bar");

if (serviceProgressBar) {
    const progress = serviceProgressBar.dataset.progress;
    serviceProgressBar.style.width = progress + "%";
}