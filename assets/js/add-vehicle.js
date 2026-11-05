
        const vehicleImageInput = document.getElementById("vehicle_image");

        const vehicleImagePreview = document.getElementById("vehicle-image-preview");

        const vehicleUploadPlaceholder = document.getElementById("vehicle-upload-placeholder");

        const removeImageButton = document.getElementById("remove-image-btn");


        vehicleImageInput.addEventListener("change", function () {

            const file = vehicleImageInput.files[0];


            if (file) {

                const imageUrl = URL.createObjectURL(file);

                vehicleImagePreview.src = imageUrl;

                vehicleImagePreview.style.display = "block";

                vehicleUploadPlaceholder.style.display = "none";

            }

        });


        removeImageButton.addEventListener("click", function () {

            vehicleImageInput.value = "";

            vehicleImagePreview.src = "";

            vehicleImagePreview.style.display = "none";

            vehicleUploadPlaceholder.style.display = "block";

        });

    const makeInput = document.getElementById("vehicle_make");
    const modelInput = document.getElementById("vehicle_model");
    const yearSelect = document.getElementById("vehicle_year");

    const makeOptions = document.getElementById("make-options");
    const modelOptions = document.getElementById("model-options");

    //Search vehicle makes
    makeInput.addEventListener("input", async function () {
        modelInput.value = "";
        modelOptions.innerHTML = "";
        yearSelect.innerHTML = '<option value="">Select Make and Model First</option>';

        const response = await fetch("/api/vehicle-makes?search=" +
            encodeURIComponent(makeInput.value));

        const makes = await response.json();

        makeOptions.innerHTML = "";

        makes.forEach(function (vehicle) {
            const option = document.createElement("option");
            option.value = vehicle.make;
            makeOptions.appendChild(option);
        });
    });

        
 //Search models for the selected make
let modelSearchTimer;

modelInput.addEventListener("input", function () {
    clearTimeout(modelSearchTimer);

    yearSelect.innerHTML = '<option value="">Select Model First</option>';
    modelOptions.innerHTML = "";

    modelSearchTimer = setTimeout(async function () {
        const make = makeInput.value;
        const search = modelInput.value;

        if (!make || !search) return;

        const response = await fetch("/api/vehicle-models?make=" +
            encodeURIComponent(make) +
            "&search=" + encodeURIComponent(search));

        const models = await response.json();

        if (makeInput.value !== make || modelInput.value !== search) return;

        models.forEach(function (vehicle) {
            const option = document.createElement("option");
            option.value = vehicle.model;
            modelOptions.appendChild(option);
        });

    }, 300);
});


    //Load available years
    modelInput.addEventListener("change", async function () {
        const response = await fetch("/api/vehicle-years?make=" +
            encodeURIComponent(makeInput.value) +
            "&model=" + encodeURIComponent(modelInput.value));

        const years = await response.json();

        yearSelect.innerHTML = '<option value="">Select Year</option>';

        years.forEach(function (vehicle) {
            const option = document.createElement("option");
            option.value = vehicle.year;
            option.textContent = vehicle.year;
            yearSelect.appendChild(option);
        });
    });
        
 //Vehicle version, body style and engine
const trimSelect = document.getElementById("vehicle_submodel");
const bodySelect = document.getElementById("vehicle_body_style");
const engineSelect = document.getElementById("vehicle_engine");
const modelCodeInput = document.getElementById("vehicle_model_code");
const fuelSelect = document.getElementById("fuel_type");
const transmissionSelect = document.getElementById("transmission");

let vehicleVariants = [];

//Reset vehicle details when make, model or year changes
function resetVariants() {
    vehicleVariants = [];
    trimSelect.innerHTML = '<option value="">Select Year First</option>';
    bodySelect.innerHTML = '<option value="">Select Year First</option>';
    engineSelect.innerHTML = '<option value="">Select Year First</option>';
    modelCodeInput.value = "";
    fuelSelect.value = "";
    transmissionSelect.value = "";
}

makeInput.addEventListener("input", resetVariants);
modelInput.addEventListener("input", resetVariants);

//Load vehicle variants after choosing a year
yearSelect.addEventListener("change", async function () {
    resetVariants();

    if (!yearSelect.value) return;

    const make = makeInput.value;
    const model = modelInput.value;
    const year = yearSelect.value;

    const response = await fetch("/api/vehicle-variants?make=" +
        encodeURIComponent(make) +
        "&model=" + encodeURIComponent(model) +
        "&year=" + encodeURIComponent(year));

    const variants = await response.json();

    if (makeInput.value !== make ||
        modelInput.value !== model ||
        yearSelect.value !== year) return;

    vehicleVariants = variants;

    trimSelect.innerHTML = '<option value="">Select Trim (Optional)</option>';

    const trims = [...new Set(variants.map(v => v.submodel).filter(Boolean))];

    trims.forEach(function (trim) {
        const option = document.createElement("option");
        option.value = trim;
        option.textContent = trim;
        trimSelect.appendChild(option);
    });

    updateVariantOptions();
});

//Update body styles and engines
function updateVariantOptions() {
    const matching = vehicleVariants.filter(function (vehicle) {
        return !trimSelect.value || vehicle.submodel === trimSelect.value;
    });

    bodySelect.innerHTML = '<option value="">Select Body Style (Optional)</option>';
    engineSelect.innerHTML = '<option value="">Select Engine (Optional)</option>';
    modelCodeInput.value = "";
    fuelSelect.value = "";
    transmissionSelect.value = "";

    const bodies = [...new Set(matching.map(v => v.body_type).filter(Boolean))];

    bodies.forEach(function (body) {
        const option = document.createElement("option");
        option.value = body;
        option.textContent = body;
        bodySelect.appendChild(option);
    });

    updateEngineOptions();
}

//Filter engines by selected trim and body style
function updateEngineOptions() {
    engineSelect.innerHTML = '<option value="">Select Engine (Optional)</option>';

    const matching = vehicleVariants.filter(function (vehicle) {
        return (!trimSelect.value || vehicle.submodel === trimSelect.value) &&
               (!bodySelect.value || vehicle.body_type === bodySelect.value);
    });

    const engines = [...new Set(
        matching.map(v => v.engine_cc).filter(v => v !== null && v > 0)
    )];

    engines.forEach(function (cc) {
        const option = document.createElement("option");
        option.value = cc;
        option.textContent = cc + "cc (" + (cc / 1000).toFixed(1) + "L)";
        engineSelect.appendChild(option);
    });

    updateEngineDetails();
}

//Automatically fill vehicle information when available
function updateEngineDetails() {
    modelCodeInput.value = "";
    fuelSelect.value = "";
    transmissionSelect.value = "";

    if (!engineSelect.value) return;

    const matching = vehicleVariants.filter(function (vehicle) {
        return (!trimSelect.value || vehicle.submodel === trimSelect.value) &&
               (!bodySelect.value || vehicle.body_type === bodySelect.value) &&
               String(vehicle.engine_cc) === engineSelect.value;
    });

    const codes = [...new Set(matching.map(v => v.model_code).filter(Boolean))];
    const fuels = [...new Set(matching.map(v => v.fuel_type).filter(Boolean))];
    const transmissions = [...new Set(matching.map(v => v.transmission).filter(Boolean))];

    if (codes.length === 1) {
        modelCodeInput.value = codes[0];
    }

    if (fuels.length === 1) {
        const fuel = fuels[0].toUpperCase();

        if (fuel.includes("HYBRID")) fuelSelect.value = "Hybrid";
        else if (fuel.includes("DIESEL")) fuelSelect.value = "Diesel";
        else if (fuel.includes("ELECTRIC")) fuelSelect.value = "Electric";
        else if (fuel.includes("PETROL")) fuelSelect.value = "Petrol";
    }

    if (transmissions.length === 1) {
        const transmission = transmissions[0].toUpperCase();

        if (transmission.includes("MANUAL")) transmissionSelect.value = "Manual";
        else if (transmission.includes("AUTO") ||
                 transmission.includes("CVT") ||
                 transmission.includes("VARIABLE")) {
            transmissionSelect.value = "Automatic";
        }
    }
}

trimSelect.addEventListener("change", updateVariantOptions);
bodySelect.addEventListener("change", updateEngineOptions);
engineSelect.addEventListener("change", updateEngineDetails);