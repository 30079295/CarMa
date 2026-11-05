
/* Dashboard Vehicle Carousel */

const vehicleCards = document.querySelectorAll(".vehicle-carousel-item");
const previousButton = document.getElementById("previousVehicles");
const nextButton = document.getElementById("nextVehicles");
const pageNumber = document.getElementById("vehiclePageNumber");
const vehicleControls = document.getElementById("vehicleControls");

let currentPage = 0;

//Number of vehicles displayed for each screen size
function getVehiclesPerPage() {
    if (window.innerWidth < 768) {
        return 1;
    }

    if (window.innerWidth < 992) {
        return 2;
    }

    return 4;
}

//Display vehicles for the current page
function showVehicles() {
    const perPage = getVehiclesPerPage();
    const totalPages = Math.ceil(vehicleCards.length / perPage);

    currentPage = Math.min(currentPage, Math.max(0, totalPages - 1));

    vehicleCards.forEach(function(card, index) {
        const start = currentPage * perPage;
        const end = start + perPage;

        if (index >= start && index < end) {
            card.style.display = "flex";
        } else {
            card.style.display = "none";
        }
    });

    vehicleControls.style.display = totalPages > 1 ? "flex" : "none";

    pageNumber.textContent = (currentPage + 1) + " / " + totalPages;

    previousButton.disabled = currentPage === 0;
    nextButton.disabled = currentPage >= totalPages - 1;
}

previousButton.addEventListener("click", function() {
    currentPage--;
    showVehicles();
});

nextButton.addEventListener("click", function() {
    currentPage++;
    showVehicles();
});

window.addEventListener("resize", showVehicles);

showVehicles();
