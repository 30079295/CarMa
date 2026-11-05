
        const vehicleImageInput = document.getElementById("vehicle_image");
        const vehicleImagePreview = document.getElementById("vehicle-image-preview");
        const vehicleUploadPlaceholder = document.getElementById("vehicle-upload-placeholder");
        const removeImageButton = document.getElementById("remove-image-btn");
        const removeVehicleImage = document.getElementById("remove_vehicle_image");

        let previewUrl = null;

        vehicleImageInput.addEventListener("change", function () {
            const file = vehicleImageInput.files[0];

            if (file) {
                if (previewUrl) {
                    URL.revokeObjectURL(previewUrl);
                }

                previewUrl = URL.createObjectURL(file);
                vehicleImagePreview.src = previewUrl;
                vehicleImagePreview.style.display = "block";
                vehicleImagePreview.hidden = false;
                vehicleUploadPlaceholder.style.display = "none";
                removeVehicleImage.value = "no";
            }
        });

        removeImageButton.addEventListener("click", function () {
            vehicleImageInput.value = "";

            if (previewUrl) {
                URL.revokeObjectURL(previewUrl);
                previewUrl = null;
            }

            vehicleImagePreview.removeAttribute("src");
            vehicleImagePreview.style.display = "none";
            vehicleImagePreview.hidden = true;
            vehicleUploadPlaceholder.style.display = "block";

            removeVehicleImage.value = "yes";
        });