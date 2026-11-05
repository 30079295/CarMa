//Confirm when correcting a vehicle odometer to a lower reading
const odometerForm = document.getElementById("odometerForm");
const odometerInput = document.getElementById("newOdometer");

odometerForm.addEventListener("submit", function(event) {
    const currentKm = Number(odometerInput.dataset.currentKm);
    const newKm = Number(odometerInput.value);

    if (newKm < currentKm) {
        const confirmed = confirm(
            "You are lowering the vehicle odometer from " +
            currentKm.toLocaleString() + " km to " +
            newKm.toLocaleString() +
            " km. Are you sure this is correct?"
        );

        if (!confirmed) {
            event.preventDefault();
        }
    }
});


//Open the odometer popup if the server returns an error
const error = new URLSearchParams(window.location.search).get("error");

if (error === "odometer-invalid" || error === "odometer-lower") {
    const odometerModal = new bootstrap.Modal(
        document.getElementById("updateOdometerModal")
    );

    odometerModal.show();
}


//Search and sort vehicle invoices
const searchInput = document.getElementById("invoice-search");
const searchButton = document.getElementById("invoice-search-btn");
const sortSelect = document.getElementById("invoice-sort");
const invoiceTabs = document.querySelectorAll(".tab-pane");

function updateInvoices() {
    const searchText = searchInput.value.toLowerCase();
    const sortValue = sortSelect.value;

    invoiceTabs.forEach(function(tab) {
        const invoiceCards = tab.querySelectorAll(".invoice-card[data-date]");
        const cardsArray = Array.from(invoiceCards);

        cardsArray.sort(function(a, b) {
            if (sortValue === "recent") {
                return new Date(b.dataset.added) - new Date(a.dataset.added);
            }

            if (sortValue === "newest") {
                return new Date(b.dataset.date) - new Date(a.dataset.date);
            }

            if (sortValue === "oldest") {
                return new Date(a.dataset.date) - new Date(b.dataset.date);
            }

            if (sortValue === "highest") {
                return parseFloat(b.dataset.cost) - parseFloat(a.dataset.cost);
            }

            if (sortValue === "lowest") {
                return parseFloat(a.dataset.cost) - parseFloat(b.dataset.cost);
            }

            return 0;
        });

        cardsArray.forEach(function(card) {
            tab.appendChild(card);
        });

        cardsArray.forEach(function(card) {
            const invoiceNumber = card.dataset.invoiceNumber.toLowerCase();
            const company = card.dataset.company.toLowerCase();
            const category = card.dataset.category.toLowerCase();

            const matchesSearch =
                invoiceNumber.includes(searchText) ||
                company.includes(searchText) ||
                category.includes(searchText);

            card.style.display = matchesSearch ? "" : "none";
        });
    });
}

searchButton.addEventListener("click", updateInvoices);
sortSelect.addEventListener("change", updateInvoices);
searchInput.addEventListener("input", updateInvoices);

updateInvoices();