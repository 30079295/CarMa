
//Get the elements from the HTML
const invoiceFileInput = document.getElementById("invoice_file");
const invoiceForm = invoiceFileInput.closest("form");
const invoicePreview = document.getElementById("invoice_preview");
const invoiceZoomImage = document.getElementById("invoice_zoom_image");
const previewColumn = document.getElementById("invoice_preview_column");
const informationColumn = document.getElementById("invoice_information_column");

function updatePreviewVisibility() {
    const hasImage = invoiceFileInput.files.length > 0;

    previewColumn.hidden = !hasImage;
    informationColumn.classList.toggle("col-lg-12", !hasImage);
    informationColumn.classList.toggle("col-lg-7", hasImage);
}

const previousInvoiceButton = document.getElementById("previous_invoice");
const nextInvoiceButton = document.getElementById("next_invoice");
const invoiceCounter = document.getElementById("invoice_counter");
const invoiceNavigation = document.getElementById("invoice_navigation");

const tableEditor = document.getElementById("invoice_table_editor");
const textFallback = document.getElementById("invoice_text_fallback");
const tableHead = document.getElementById("invoice_table_head");
const tableBody = document.getElementById("invoice_table_body");
const addRowButton = document.getElementById("add_invoice_row");

const invoiceItemsInput = document.getElementById("invoice_items");
const ocrTextInput = document.getElementById("ocr_text");
const notesEditor = document.getElementById("invoice_notes_editor");

const sectionsEditor = document.getElementById("invoice_sections_editor");
const sectionsList = document.getElementById("invoice_sections_list");
const addSectionButton = document.getElementById("add_invoice_section");

const rescanButton = document.querySelector(".rescan-ocr-btn");

const serviceCheckbox = document.getElementById("category-service");
const serviceDetailsSection = document.getElementById("service_details_section");
const serviceDescriptionInput = document.getElementById("service_description");
const invoiceOdometerInput = document.getElementById("invoice_odometer");
const confirmLargeOdometer = document.getElementById("confirm_large_odometer");

//Confirm large odometer increases before saving
invoiceForm.addEventListener("submit", function(event) {
    const currentKm = Number(invoiceOdometerInput.dataset.currentKm);
    const invoiceKm = Number(invoiceOdometerInput.value);

    confirmLargeOdometer.value = "off";

    if (invoiceOdometerInput.value === "") {
        return;
    }

    if (invoiceOdometerInput.dataset.currentKm !== "" &&
        Number.isInteger(invoiceKm) &&
        invoiceKm - currentKm > 20000) {

        const confirmed = confirm(
            "This invoice is " +
            (invoiceKm - currentKm).toLocaleString() +
            " km higher than the vehicle's current odometer. " +
            "Are you sure this reading is correct?"
        );

        if (!confirmed) {
            event.preventDefault();
            return;
        }

        confirmLargeOdometer.value = "on";
    }
});

const countsAsServiceInput = document.getElementById("counts_as_service");

//Labels for the OCR table columns
const columnLabels = {
    part_number: "Part No.",
    description: "Description",
    quantity: "Qty / Hours",
    unit_price: "Unit Price",
    gst: "GST",
    total: "Total"
};

let uploadedPages = [];
let invoicePages = [];
let currentPageIndex = 0;
let currentPreviewUrl = null;

//Show a message if the browser cannot preview an image
const previewMessage = document.createElement("p");
previewMessage.textContent = "Preview unavailable for this image. OCR scanning is still supported.";
previewMessage.hidden = true;

invoicePreview.insertAdjacentElement("afterend", previewMessage);

invoicePreview.addEventListener("load", function () {
    invoicePreview.hidden = false;
    previewMessage.hidden = true;
});

invoicePreview.addEventListener("error", function () {
    invoicePreview.hidden = true;
    previewMessage.hidden = false;
});

let scanNumber = 0;

let invoiceTable = {
    columns: [],
    rows: []
};

let usingTableEditor = false;

//Track whether invoice images are being prepared
let preparingInvoice = false;

//Helper functions
function asText(value) {
    if (value === null || value === undefined) {
        return "";
    }

    return String(value);
}

function columnLabel(column) {
    return columnLabels[column] || column.replaceAll("_", " ");
}

function getTableColumns(table, rows) {
    const columns = Array.isArray(table.columns)
        ? [...table.columns]
        : [];

    rows.forEach(function(row) {
        Object.keys(row).forEach(function(column) {
            if (asText(row[column]).trim() && !columns.includes(column)) {
                columns.push(column);
            }
        });
    });

    return columns;
}

function updateServiceDetailsVisibility() {
    const isService = serviceCheckbox.checked;

    serviceDetailsSection.hidden = !isService;
    serviceDescriptionInput.required = isService;
    serviceDescriptionInput.disabled = !isService;
    countsAsServiceInput.disabled = !isService;
    invoiceOdometerInput.required = isService && countsAsServiceInput.checked;
}

//Invoice blocks
function getPageBlocks(page) {
    if (!page) return [];

    if (!Array.isArray(page.blocks)) {
        page.blocks = [];

        if (page.table && page.table.rows && page.table.rows.length) {
            page.blocks.push({
                type: "table",
                heading: page.table.heading || "",
                columns: [...(page.table.columns || [])],
                rows: page.table.rows.map(row => ({ ...row }))
            });
        }

        (page.sections || []).forEach(function(section) {
            page.blocks.push({
                type: "text",
                heading: section.heading || "",
                content: section.content || ""
            });
        });
    }

    return page.blocks;
}

//Keep the old table and sections data for Flask
function saveOldInvoiceFields() {
    invoicePages.forEach(function (page) {
        if (!Array.isArray(page.blocks)) return;

        const firstTable = page.blocks.find(block => block.type === "table");

        if (firstTable) {
            page.table = {
                columns: firstTable.columns,
                rows: firstTable.rows
            };
        } else {
            page.table = {
                columns: [],
                rows: []
            };
        }

        page.sections = page.blocks
            .filter(block => block.type === "text")
            .map(function (block) {
                return {
                    type: "text",
                    heading: block.heading,
                    content: block.content
                };
            });
    });
}

//Save edited data
function syncInvoiceData() {
    saveOldInvoiceFields();

    const savedInvoice = {
        columns: invoiceTable.columns,
        rows: invoiceTable.rows,
        pages: invoicePages
    };

    invoiceItemsInput.value = JSON.stringify(savedInvoice);

    //Keep the old OCR text field populated
    if (usingTableEditor) {
        ocrTextInput.value = invoiceTable.rows.map(function (row) {
            return invoiceTable.columns.map(function (column) {
                const value = asText(row[column]).trim();

                if (!value) return "";

                if (column === "quantity") return "Qty: " + value;
                if (column === "unit_price") return "Unit Price: " + value;
                if (column === "gst") return "GST: " + value;
                if (column === "total") return "Total: " + value;

                return value;
            }).filter(Boolean).join(" | ");
        }).filter(Boolean).join("\n");
    }
}

//Show the correct editor
function updateSectionEditorVisibility() {
    const page = invoicePages[currentPageIndex];
    const hasBlocks = page && getPageBlocks(page).length > 0;

    sectionsEditor.hidden = !page;
    tableEditor.hidden = hasBlocks || !usingTableEditor;
    textFallback.hidden = hasBlocks || usingTableEditor;
}

//Build the editable sections
function renderInvoiceSections() {
    sectionsList.replaceChildren();

    const page = invoicePages[currentPageIndex];

    if (!page) {
        updateSectionEditorVisibility();
        return;
    }

    const blocks = getPageBlocks(page);

    blocks.forEach(function (block, index) {
        const sectionBox = document.createElement("div");
        sectionBox.className = "ocr-section mb-3";

        //Section heading
        const headingLabel = document.createElement("label");
        headingLabel.className = "form-label";
        headingLabel.textContent = "Section Heading";

        const headingInput = document.createElement("input");
        headingInput.type = "text";
        headingInput.className = "form-control mb-2";
        headingInput.placeholder = "Heading (optional)";
        headingInput.value = block.heading || "";

        headingInput.addEventListener("input", function () {
            block.heading = headingInput.value;
            syncInvoiceData();
        });

        headingLabel.appendChild(headingInput);
        sectionBox.appendChild(headingLabel);

        //Table blocks
        if (block.type === "table") {
            if (!Array.isArray(block.columns)) block.columns = [];
            if (!Array.isArray(block.rows)) block.rows = [];

            //Save custom headings separately from table data
            if (!block.column_labels) {
                block.column_labels = {};
            }

            const scroll = document.createElement("div");
            scroll.className = "invoice-table-scroll mt-2";

            const table = document.createElement("table");
            table.className = "table invoice-edit-table";

            const tableHeader = document.createElement("thead");
            const headerRow = document.createElement("tr");

            //Editable column names
            block.columns.forEach(function (column) {
                const th = document.createElement("th");

                const input = document.createElement("input");
                input.type = "text";
                input.className = "form-control";
                input.placeholder = "Column heading";

                if (Object.hasOwn(block.column_labels, column)) {
                    input.value = block.column_labels[column];
                } else {
                    input.value = columnLabel(column);
                }

                input.addEventListener("input", function () {
                    block.column_labels[column] = input.value;
                    syncInvoiceData();
                });

                th.appendChild(input);
                headerRow.appendChild(th);
            });

            const actionsHeading = document.createElement("th");
            actionsHeading.textContent = "Actions";
            headerRow.appendChild(actionsHeading);

            tableHeader.appendChild(headerRow);
            table.appendChild(tableHeader);

            //Table rows
            const tableBodyElement = document.createElement("tbody");

            block.rows.forEach(function (row, rowIndex) {
                const tr = document.createElement("tr");

                block.columns.forEach(function (column) {
                    const td = document.createElement("td");
                    td.dataset.column = column;

                    const input = document.createElement("input");
                    input.type = "text";
                    input.value = asText(row[column]);

                    input.addEventListener("input", function () {
                        row[column] = input.value;
                        syncInvoiceData();
                    });

                    td.appendChild(input);

                    //Remove button for each cell
                    const removeCell = document.createElement("button");
                    removeCell.type = "button";
                    removeCell.className = "btn btn-sm btn-outline-danger mt-2";
                    removeCell.textContent = "Remove";

                    removeCell.addEventListener("click", function () {
                        row[column] = "";
                        input.value = "";
                        syncInvoiceData();
                    });

                    td.appendChild(removeCell);
                    tr.appendChild(td);
                });

                //Remove the entire row
                const actionCell = document.createElement("td");

                const removeRow = document.createElement("button");
                removeRow.type = "button";
                removeRow.className = "btn btn-sm btn-outline-danger";
                removeRow.textContent = "Remove Row";

                removeRow.addEventListener("click", function () {
                    block.rows.splice(rowIndex, 1);
                    renderInvoiceSections();
                    syncInvoiceData();
                });

                actionCell.appendChild(removeRow);
                tr.appendChild(actionCell);
                tableBodyElement.appendChild(tr);
            });

            table.appendChild(tableBodyElement);
            scroll.appendChild(table);
            sectionBox.appendChild(scroll);

            //Add Row button
            const addRow = document.createElement("button");
            addRow.type = "button";
            addRow.className = "btn btn-primary mt-2 me-2";
            addRow.textContent = "+ Add Row";

            addRow.addEventListener("click", function () {
                const newRow = {};

                block.columns.forEach(function (column) {
                    newRow[column] = "";
                });

                block.rows.push(newRow);
                renderInvoiceSections();
                syncInvoiceData();
            });

            sectionBox.appendChild(addRow);

            //Add Column button
            const addColumn = document.createElement("button");
            addColumn.type = "button";
            addColumn.className = "btn btn-primary mt-2 me-2";
            addColumn.textContent = "+ Add Column";

            addColumn.addEventListener("click", function () {
                let number = 1;

                while (block.columns.includes("column_" + number)) {
                    number++;
                }

                const newColumn = "column_" + number;

                block.columns.push(newColumn);
                block.column_labels[newColumn] = "";

                block.rows.forEach(function (row) {
                    row[newColumn] = "";
                });

                renderInvoiceSections();
                syncInvoiceData();
            });

            sectionBox.appendChild(addColumn);

            //Delete Column button
            const deleteColumn = document.createElement("button");
            deleteColumn.type = "button";
            deleteColumn.className = "btn btn-outline-danger mt-2";
            deleteColumn.textContent = "- Delete Column";

            deleteColumn.addEventListener("click", function () {
                if (block.columns.length === 0) return;

                let choices = "";

                block.columns.forEach(function (column, i) {
                    const label = Object.hasOwn(block.column_labels, column)
                        ? block.column_labels[column]
                        : columnLabel(column);

                    choices += (i + 1) + ". " + (label || "(blank)") + "\n";
                });

                const answer = prompt(
                    "Enter the number of the column to delete:\n" + choices
                );

                if (answer === null) return;

                const selectedNumber = Number(answer.trim());

                if (!Number.isInteger(selectedNumber) ||
                    selectedNumber < 1 ||
                    selectedNumber > block.columns.length) {
                    alert("Please enter a valid column number.");
                    return;
                }

                const column = block.columns[selectedNumber - 1];

                if (!confirm("Delete this column and its values?")) return;

                block.columns.splice(selectedNumber - 1, 1);

                block.rows.forEach(function (row) {
                    delete row[column];
                });

                delete block.column_labels[column];

                renderInvoiceSections();
                syncInvoiceData();
            });

            sectionBox.appendChild(deleteColumn);

        } else {
            //Text sections
            const contentLabel = document.createElement("label");
            contentLabel.className = "form-label";
            contentLabel.textContent = "Content";

            const contentInput = document.createElement("textarea");
            contentInput.className = "form-control ocr-content-textarea";
            contentInput.rows = 5;
            contentInput.value = asText(block.content).replace(/\\n/g, "\n");

            contentInput.addEventListener("input", function () {
                block.content = contentInput.value;
                syncInvoiceData();
            });

            contentLabel.appendChild(contentInput);
            sectionBox.appendChild(contentLabel);
        }

        //Move and remove section buttons
        const controls = document.createElement("div");
        controls.className = "mt-2 d-flex gap-2 flex-wrap";

        const moveUp = document.createElement("button");
        moveUp.type = "button";
        moveUp.className = "btn btn-sm btn-outline-primary";
        moveUp.textContent = "Move Up";
        moveUp.disabled = index === 0;

        moveUp.addEventListener("click", function () {
            [blocks[index - 1], blocks[index]] =
                [blocks[index], blocks[index - 1]];

            renderInvoiceSections();
            syncInvoiceData();
        });

        const moveDown = document.createElement("button");
        moveDown.type = "button";
        moveDown.className = "btn btn-sm btn-outline-primary";
        moveDown.textContent = "Move Down";
        moveDown.disabled = index === blocks.length - 1;

        moveDown.addEventListener("click", function () {
            [blocks[index], blocks[index + 1]] =
                [blocks[index + 1], blocks[index]];

            renderInvoiceSections();
            syncInvoiceData();
        });

        const removeSection = document.createElement("button");
        removeSection.type = "button";
        removeSection.className = "btn btn-sm btn-outline-danger";

        removeSection.textContent = block.type === "table"
            ? "Remove Table"
            : "Remove Section";

        removeSection.addEventListener("click", function () {
            if (!confirm("Remove this section?")) return;

            blocks.splice(index, 1);
            renderInvoiceSections();
            syncInvoiceData();
        });

        controls.appendChild(moveUp);
        controls.appendChild(moveDown);
        controls.appendChild(removeSection);

        sectionBox.appendChild(controls);
        sectionsList.appendChild(sectionBox);
    });

    updateSectionEditorVisibility();
}

//Add new sections and tables
addSectionButton.addEventListener("click", function () {
    const page = invoicePages[currentPageIndex];
    if (!page) return;

    getPageBlocks(page).push({
        type: "text",
        heading: "New Section",
        content: ""
    });

    renderInvoiceSections();
    syncInvoiceData();

    sectionsList.lastElementChild?.querySelector("input")?.focus();
});

//Create the Add Table button
const addTableButton = document.createElement("button");
addTableButton.type = "button";
addTableButton.className = addSectionButton.className;
addTableButton.classList.add("ms-2");
addTableButton.textContent = "+ Add Table";

addSectionButton.insertAdjacentElement("afterend", addTableButton);

addTableButton.addEventListener("click", function () {
    const page = invoicePages[currentPageIndex];
    if (!page) return;

    getPageBlocks(page).push({
        type: "table",
        heading: "New Table",
        columns: ["description"],
        rows: [],
        column_labels: {
            description: "Description"
        }
    });

    renderInvoiceSections();
    syncInvoiceData();
});

//Page navigation
function updateInvoiceNavigation() {
    const totalPages = uploadedPages.length;

    invoiceNavigation.hidden = totalPages <= 1;

    invoiceCounter.textContent = totalPages > 0
        ? `Page ${currentPageIndex + 1} of ${totalPages}`
        : "Page 0 of 0";

    previousInvoiceButton.disabled = currentPageIndex === 0;
    nextInvoiceButton.disabled = currentPageIndex >= totalPages - 1;
}

function loadCurrentInvoice() {
    const page = uploadedPages[currentPageIndex];
    if (!page) return;

    if (currentPreviewUrl) {
        URL.revokeObjectURL(currentPreviewUrl);
    }

    //Reset the preview before loading the next page
    invoicePreview.hidden = false;
    previewMessage.hidden = true;

    currentPreviewUrl = URL.createObjectURL(page.file);
    invoicePreview.src = currentPreviewUrl;
    invoiceZoomImage.src = currentPreviewUrl;

    const ocrPage = invoicePages[currentPageIndex];

    if (ocrPage) {
        loadInvoiceContent(
            ocrPage.table,
            ocrPage.content_text ?? ocrPage.raw_text ?? ""
        );

        notesEditor.value = ocrPage.notes ?? "";
        renderInvoiceSections();
    }
}

//Old table editor and text fallback
function renderInvoiceTable() {
    tableHead.replaceChildren();
    tableBody.replaceChildren();

    invoiceTable.columns.forEach(function (column) {
        const heading = document.createElement("th");
        heading.scope = "col";
        heading.textContent = columnLabel(column);
        tableHead.appendChild(heading);
    });

    const actionsHeading = document.createElement("th");
    actionsHeading.textContent = "Actions";
    tableHead.appendChild(actionsHeading);

    invoiceTable.rows.forEach(function (row, rowIndex) {
        const tr = document.createElement("tr");

        invoiceTable.columns.forEach(function (column) {
            const td = document.createElement("td");
            td.dataset.column = column;

            const input = document.createElement("input");
            input.type = "text";
            input.value = asText(row[column]);

            input.addEventListener("input", function () {
                row[column] = input.value;
                syncInvoiceData();
            });

            td.appendChild(input);
            tr.appendChild(td);
        });

        const actionCell = document.createElement("td");
        const removeRow = document.createElement("button");
        removeRow.type = "button";
        removeRow.className = "btn btn-sm btn-outline-danger";
        removeRow.textContent = "Remove";

        removeRow.addEventListener("click", function () {
            invoiceTable.rows.splice(rowIndex, 1);
            renderInvoiceTable();
        });

        actionCell.appendChild(removeRow);
        tr.appendChild(actionCell);
        tableBody.appendChild(tr);
    });

    syncInvoiceData();
}

function loadInvoiceContent(table, rawText) {
    const source = table && typeof table === "object" ? table : {};

    const rows = Array.isArray(source.rows)
        ? source.rows.filter(row => row && typeof row === "object")
        : [];

    invoiceTable = {
        ...source,
        columns: getTableColumns(source, rows),
        rows: rows.map(row => ({ ...row }))
    };

    usingTableEditor =
        invoiceTable.rows.length > 0 &&
        invoiceTable.columns.length > 0;

    tableEditor.hidden = !usingTableEditor;
    textFallback.hidden = usingTableEditor;

    if (usingTableEditor) {
        renderInvoiceTable();
    } else {
        tableHead.replaceChildren();
        tableBody.replaceChildren();

        ocrTextInput.value = rawText || "";
        syncInvoiceData();
    }
}

//Add Row button
addRowButton.addEventListener("click", function () {
    const newRow = {};

    invoiceTable.columns.forEach(function (column) {
        newRow[column] = "";
    });

    invoiceTable.rows.push(newRow);
    renderInvoiceTable();

    tableBody.lastElementChild?.querySelector("input")?.focus();
});

//Save changes to the raw OCR text
ocrTextInput.addEventListener("input", function () {
    const page = invoicePages[currentPageIndex];
    if (!page) return;

    page.content_text = ocrTextInput.value;
    syncInvoiceData();
});

//Save page notes
notesEditor.addEventListener("input", function () {
    const page = invoicePages[currentPageIndex];
    if (!page) return;

    page.notes = notesEditor.value;
    syncInvoiceData();
});

//Scan invoice with PaddleOCR
const ocrLoading = document.getElementById("ocr_loading");
const ocrWarning = document.getElementById("ocr_warning");

async function scanInvoice() {
    const file = uploadedPages[currentPageIndex]?.file;
    if (!file) return;

    const thisScan = ++scanNumber;

    if (file.type.startsWith("image/")) {
        if (currentPreviewUrl) {
            URL.revokeObjectURL(currentPreviewUrl);
        }

        currentPreviewUrl = URL.createObjectURL(file);
        invoicePreview.src = currentPreviewUrl;
        invoiceZoomImage.src = currentPreviewUrl;
    }

    const formData = new FormData();

    uploadedPages.forEach(function (page) {
        formData.append("invoice_file", page.file);
    });

    if (rescanButton) {
        rescanButton.disabled = true;
    }

    ocrLoading.hidden = false;
    ocrWarning.hidden = true;
    ocrWarning.textContent = "";

    try {
        const response = await fetch("/scan-invoice", {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (thisScan !== scanNumber) return;

        if (!response.ok) {
            throw new Error(data.error || "OCR scan failed");
        }

        //Get all the scanned invoice pages
        const pages = data.table?.pages || [];

        const hasReadableText = pages.some(function (page) {
            const text = page.raw_text || page.content_text || "";
            const hasTable = (page.table?.rows?.length || 0) > 0;
            const hasSections = (page.sections?.length || 0) > 0;
            const hasBlocks = (page.blocks?.length || 0) > 0;

            return text.trim().length >= 10 || hasTable || hasSections || hasBlocks;
        });

        if (!hasReadableText && !String(data.raw_text || "").trim()) {
            ocrWarning.textContent =
                "Unable to read invoice. Please take another photo with better lighting and make sure the text is clear.";
            ocrWarning.hidden = false;
        } else {
            const missingDetails = [];

            if (!data.company_name) missingDetails.push("company name");
            if (!data.date) missingDetails.push("invoice date");
            if (data.cost === "" || data.cost == null) missingDetails.push("total amount");

            if (missingDetails.length > 0) {
                ocrWarning.textContent =
                    "Some invoice details could not be detected (" +
                    missingDetails.join(", ") +
                    "). Please check and correct the information before saving.";

                ocrWarning.hidden = false;
            }
        }

        //Company information
        const companyFields = [
            "company_name",
            "company_address",
            "company_phone",
            "company_email",
            "gst_number",
            "invoice_number"
        ];

        companyFields.forEach(function (field) {
            document.getElementById(field).value = data[field] || "";
        });

        //Invoice details
        document.querySelector('input[name="date"]').value = data.date || "";
        document.querySelector('input[name="cost"]').value = data.cost ?? "";

        //Fill the odometer from OCR if one was found
        invoiceOdometerInput.value = data.invoice_odometer ?? "";

        //Fill in the service description from OCR
        serviceDescriptionInput.value = data.service_description || "";

        //Invoice categories
        const categories = asText(data.category)
            .split("/")
            .map(value => value.trim());

        ["Service", "Repair", "Modification"].forEach(function (category) {
            const checkbox = document.getElementById(
                "category-" + category.toLowerCase()
            );

            checkbox.checked = categories.includes(category);
        });

        //Suggest if the invoice counts as a general service
        countsAsServiceInput.checked =
            serviceCheckbox.checked && data.counts_as_service === true;

        updateServiceDetailsVisibility();

        //Store the OCR results for all pages
        invoicePages = data.table?.pages || [];
        currentPageIndex = 0;

        const currentPage = invoicePages[currentPageIndex];

        if (currentPage) {
            notesEditor.value = currentPage.notes ?? "";

            loadInvoiceContent(
                currentPage.table,
                currentPage.content_text ?? currentPage.raw_text ?? ""
            );
        } else {
            notesEditor.value = "";

            loadInvoiceContent(
                data.table || {},
                data.raw_text || ""
            );
        }

        renderInvoiceSections();
        updateInvoiceNavigation();

    } catch (error) {
        if (thisScan === scanNumber) {
            alert(error.message || "OCR scan failed");
        }
    } finally {
        if (thisScan === scanNumber) {
            ocrLoading.hidden = true;

            if (rescanButton) {
                rescanButton.disabled = false;
            }
        }
    }
}

//Event listeners
nextInvoiceButton.addEventListener("click", function () {
    if (currentPageIndex >= uploadedPages.length - 1) return;

    currentPageIndex++;
    loadCurrentInvoice();
    updateInvoiceNavigation();
});

previousInvoiceButton.addEventListener("click", function () {
    if (currentPageIndex <= 0) return;

    currentPageIndex--;
    loadCurrentInvoice();
    updateInvoiceNavigation();
});

serviceCheckbox.addEventListener("change", updateServiceDetailsVisibility);
countsAsServiceInput.addEventListener("change", updateServiceDetailsVisibility);

updateServiceDetailsVisibility();

//Compress invoice images before sending them to Flask
async function compressInvoiceImage(file) {
    //Browser canvas may not support HEIC/HEIF images
    if (/\.(heic|heif)$/i.test(file.name)) {
        return file;
    }

    const image = await createImageBitmap(file);
    const canvas = document.createElement("canvas");

    //Keep invoice text readable while reducing large images
    const maxSize = 2200;
    const scale = Math.min(1, maxSize / Math.max(image.width, image.height));

    canvas.width = Math.round(image.width * scale);
    canvas.height = Math.round(image.height * scale);

    const context = canvas.getContext("2d");
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    image.close();

    const blob = await new Promise(resolve => {
        canvas.toBlob(resolve, "image/jpeg", 0.80);
    });

    if (!blob) {
        throw new Error("Unable to compress invoice image.");
    }

    const compressedFile = new File(
        [blob],
        file.name.replace(/\.[^.]+$/, "") + ".jpg",
        { type: "image/jpeg" }
    );

    //Keep the original if compression makes it bigger
    return compressedFile.size < file.size ? compressedFile : file;
}

//Selected invoice images
invoiceFileInput.addEventListener("change", async function () {
    const originalFiles = Array.from(invoiceFileInput.files)
        .sort((a, b) =>
            a.name.localeCompare(b.name, undefined, { numeric: true })
        );

    if (!originalFiles.length) return;

    //Prevent scanning or saving while preparing images
    preparingInvoice = true;
    invoiceFileInput.disabled = true;

    try {
        const compressedFiles = [];

        for (const file of originalFiles) {
            const compressed = await compressInvoiceImage(file);
            compressedFiles.push(compressed);
        }

        //Allow room for other form data within Vercel's limit
        const totalSize = compressedFiles.reduce(
            (total, file) => total + file.size, 0
        );

        if (totalSize > 3.5 * 1024 * 1024) {
            alert(
                "Invoice images are too large. Please upload fewer " +
                "pages or use smaller images."
            );

            invoiceFileInput.value = "";
            uploadedPages = [];
            updatePreviewVisibility();
            return;
        }

        //Replace the original files with the compressed files
        const dataTransfer = new DataTransfer();

        compressedFiles.forEach(function (file) {
            dataTransfer.items.add(file);
        });

        invoiceFileInput.files = dataTransfer.files;

        uploadedPages = compressedFiles.map(
            file => ({ file: file })
        );

        currentPageIndex = 0;
        invoicePages = [];
        invoiceTable = { columns: [], rows: [] };
        usingTableEditor = false;

        updatePreviewVisibility();
        updateInvoiceNavigation();

        if (uploadedPages.length > 0) {
            loadCurrentInvoice();
            await scanInvoice();
        }

    } catch (error) {
        console.error("Invoice compression failed:", error);
        alert("Unable to prepare invoice images. Please try again.");

        invoiceFileInput.value = "";
        uploadedPages = [];
        updatePreviewVisibility();

    } finally {
        preparingInvoice = false;
        invoiceFileInput.disabled = false;
    }
});

//Rescan button
if (rescanButton) {
    rescanButton.addEventListener("click", scanInvoice);
}

//Save invoice
invoiceForm.addEventListener("submit", function (event) {
    //Do not save until compression and OCR have finished
    if (preparingInvoice) {
        event.preventDefault();
        alert("Please wait for the invoice images to finish processing.");
        return;
    }

    //Warn the user if this invoice resets the service reminder
    if (serviceCheckbox.checked && countsAsServiceInput.checked) {
        const invoiceKm = invoiceOdometerInput.value;

        const message =
            "This invoice will update your general service reminder.\n" +
            "Invoice odometer: " +
            (invoiceKm ? Number(invoiceKm).toLocaleString() + " km" : "Not entered") +
            "\n\nContinue saving?";

        if (!confirm(message)) {
            event.preventDefault();
            return;
        }
    }

    syncInvoiceData();
});

//Create an empty page for manual invoice entry
invoicePages = [{
    blocks: [],
    table: { columns: [], rows: [] },
    sections: [],
    content_text: "",
    notes: ""
}];

renderInvoiceSections();

//Initialise the hidden invoice data
syncInvoiceData();
updatePreviewVisibility();
