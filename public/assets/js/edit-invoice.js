
//CarMa - Edit Invoice

const invoiceItemsInput = document.getElementById("invoice_items");
const invoiceForm = invoiceItemsInput.closest("form");

const tableEditor = document.getElementById("invoice_table_editor");
const tableHead = document.getElementById("invoice_table_head");
const tableBody = document.getElementById("invoice_table_body");
const addRowButton = document.getElementById("add_invoice_row");

const textFallback = document.getElementById("invoice_text_fallback");
const ocrTextInput = document.getElementById("ocr_text");
const notesEditor = document.getElementById("invoice_notes");

const savedInvoiceData = document.getElementById("saved_invoice_data");
const savedInvoice = JSON.parse(savedInvoiceData.textContent || "{}");

const invoicePages = Array.isArray(savedInvoice.pages)
    ? savedInvoice.pages
    : [];

let currentPageIndex = 0;

let invoiceTable = {
    columns: [],
    rows: []
};

//Page navigation
const previousPageButton = document.getElementById("edit_previous_page");
const nextPageButton = document.getElementById("edit_next_page");
const pageCounter = document.getElementById("edit_page_counter");
const pageNavigation = previousPageButton.parentElement;

//Column names used by OCR
const columnLabels = {
    part_number: "Part No.",
    description: "Description",
    quantity: "Qty / Hours",
    unit_price: "Unit Price",
    gst: "GST",
    total: "Total"
};

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

function currentPage() {
    return invoicePages[currentPageIndex];
}

//Get the saved invoice sections
function getPageBlocks(page) {
    if (!page) return [];

    //Convert older invoices into blocks
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

        (page.sections || []).forEach(function (section) {
            page.blocks.push({
                type: "text",
                heading: section.heading || "",
                content: section.content || ""
            });
        });
    }

    return page.blocks;
}

//Save invoice data
function syncInvoiceData() {
    //Keep older table and section fields updated
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

    invoiceItemsInput.value = JSON.stringify({
        ...savedInvoice,
        pages: invoicePages
    });
}

//Create the section editor
const sectionsEditor = document.getElementById("invoice_blocks_editor");
const sectionsList = document.getElementById("invoice_blocks_list");
const addSectionButton = document.getElementById("add_edit_section");
const addTableButton = document.getElementById("add_edit_table");

//Display tables and text sections
function renderInvoiceSections() {
    sectionsList.replaceChildren();

    const page = currentPage();
    if (!page) return;

    const blocks = getPageBlocks(page);

    blocks.forEach(function (block, index) {
        const sectionBox = document.createElement("div");
        sectionBox.className = "border rounded p-3 mb-3";

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

        sectionBox.appendChild(headingLabel);
        sectionBox.appendChild(headingInput);

        if (block.type === "table") {
            //Table data
            if (!Array.isArray(block.columns)) block.columns = [];
            if (!Array.isArray(block.rows)) block.rows = [];

            if (!block.column_labels) {
                block.column_labels = {};
            }

            const scroll = document.createElement("div");
            scroll.className = "table-responsive";

            const table = document.createElement("table");
            table.className = "table invoice-edit-table";

            const thead = document.createElement("thead");
            const headerRow = document.createElement("tr");

            //Editable column headings
            block.columns.forEach(function (column) {
                const th = document.createElement("th");

                const input = document.createElement("input");
                input.type = "text";
                input.className = "form-control";
                input.placeholder = "Column heading";

                if (Object.prototype.hasOwnProperty.call(block.column_labels, column)) {
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

            thead.appendChild(headerRow);
            table.appendChild(thead);

            //Table rows
            const tbody = document.createElement("tbody");

            block.rows.forEach(function (row, rowIndex) {
                const tr = document.createElement("tr");

                block.columns.forEach(function (column) {
                    const td = document.createElement("td");

                    const input = document.createElement("input");
                    input.type = "text";
                    input.className = "form-control";
                    input.value = asText(row[column]);

                    input.addEventListener("input", function () {
                        row[column] = input.value;
                        syncInvoiceData();
                    });

                    td.appendChild(input);

                    //Clear just this cell
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

                //Remove the whole row
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
                tbody.appendChild(tr);
            });

            table.appendChild(tbody);
            scroll.appendChild(table);
            sectionBox.appendChild(scroll);

            //Add row
            const addRow = document.createElement("button");
            addRow.type = "button";
            addRow.className = "btn btn-primary mt-2 me-2";
            addRow.textContent = "+ Add Row";

            addRow.addEventListener("click", function () {
                const row = {};

                block.columns.forEach(function (column) {
                    row[column] = "";
                });

                block.rows.push(row);
                renderInvoiceSections();
                syncInvoiceData();
            });

            sectionBox.appendChild(addRow);

            //Add column
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

            //Delete column
            const deleteColumn = document.createElement("button");
            deleteColumn.type = "button";
            deleteColumn.className = "btn btn-outline-danger mt-2";
            deleteColumn.textContent = "- Delete Column";

            deleteColumn.addEventListener("click", function () {
                if (!block.columns.length) return;

                let choices = "";

                block.columns.forEach(function (column, i) {
                    const label = Object.prototype.hasOwnProperty.call(
                        block.column_labels, column
                    ) ? block.column_labels[column] : columnLabel(column);

                    choices += (i + 1) + ". " + (label || "(blank)") + "\n";
                });

                const answer = prompt(
                    "Enter the number of the column to delete:\n" + choices
                );

                if (answer === null) return;

                const number = Number(answer.trim());

                if (!Number.isInteger(number) ||
                    number < 1 ||
                    number > block.columns.length) {
                    alert("Enter a valid column number.");
                    return;
                }

                if (!confirm("Delete this column and its values?")) return;

                const column = block.columns[number - 1];

                block.columns.splice(number - 1, 1);

                block.rows.forEach(function (row) {
                    delete row[column];
                });

                delete block.column_labels[column];

                renderInvoiceSections();
                syncInvoiceData();
            });

            sectionBox.appendChild(deleteColumn);

        } else {
            //Text section
            const contentLabel = document.createElement("label");
            contentLabel.className = "form-label";
            contentLabel.textContent = "Content";

            const contentInput = document.createElement("textarea");
            contentInput.className = "form-control";
            contentInput.rows = 5;
            contentInput.value = asText(block.content).replace(/\\n/g, "\n");

            contentInput.addEventListener("input", function () {
                block.content = contentInput.value;
                syncInvoiceData();
            });

            sectionBox.appendChild(contentLabel);
            sectionBox.appendChild(contentInput);
        }

        //Move up
        const controls = document.createElement("div");
        controls.className = "d-flex gap-2 mt-2 flex-wrap";

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

        //Move down
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

        //Remove table or section
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
}

//Add new sections and tables
addSectionButton.addEventListener("click", function () {
    const page = currentPage();
    if (!page) return;

    getPageBlocks(page).push({
        type: "text",
        heading: "New Section",
        content: ""
    });

    renderInvoiceSections();
    syncInvoiceData();
});

addTableButton.addEventListener("click", function () {
    const page = currentPage();
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

//Load the selected page
function loadEditPage() {
    const page = currentPage();
    if (!page) return;

    //Show the matching invoice image
    const invoicePreview = document.getElementById("edit_invoice_preview");

    if (invoicePreview) {
        invoicePreview.src = window.location.pathname.replace(
            /\/edit$/,
            "/preview/" + currentPageIndex
        );
    }

    //Show the saved sections for this page
    getPageBlocks(page);
    renderInvoiceSections();

    sectionsEditor.hidden = false;

    //Show raw OCR text only if there are no blocks
    textFallback.hidden = page.blocks.length > 0;

    ocrTextInput.value = asText(
        page.content_text ?? page.raw_text
    );

    notesEditor.value = asText(page.notes);

    //Hide old notes because the new sections contain them
    const notesField = document.getElementById("old_invoice_notes");

    if (notesField) {
        notesField.hidden = page.blocks.length > 0;
    }

    pageCounter.textContent =
        `Page ${currentPageIndex + 1} of ${invoicePages.length}`;

    previousPageButton.disabled = currentPageIndex === 0;
    nextPageButton.disabled = currentPageIndex >= invoicePages.length - 1;

    syncInvoiceData();
}

//Page navigation
previousPageButton.addEventListener("click", function () {
    if (currentPageIndex <= 0) return;

    currentPageIndex--;
    loadEditPage();
});

nextPageButton.addEventListener("click", function () {
    if (currentPageIndex >= invoicePages.length - 1) return;

    currentPageIndex++;
    loadEditPage();
});

//Save OCR text and notes
ocrTextInput.addEventListener("input", function () {
    const page = currentPage();
    if (!page) return;

    page.content_text = ocrTextInput.value;
    syncInvoiceData();
});

notesEditor.addEventListener("input", function () {
    const page = currentPage();
    if (!page) return;

    page.notes = notesEditor.value;
    syncInvoiceData();
});

//Save changes
invoiceForm.addEventListener("submit", function(event) {
    syncInvoiceData();

    //Check if the edited invoice has a higher odometer
    const invoiceOdometerInput = document.getElementById("invoice_odometer");
    const confirmLargeOdometer = document.getElementById("confirm_large_odometer");

    const currentKm = Number(invoiceOdometerInput.dataset.currentKm);
    const invoiceKm = Number(invoiceOdometerInput.value);

    confirmLargeOdometer.value = "off";

    //Only check when an odometer was entered
    if (invoiceOdometerInput.value === "") {
        return;
    }

    //Ask for confirmation if the increase is over 20,000 km
    if (Number.isInteger(invoiceKm) && invoiceKm - currentKm > 20000) {
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

//Require a description when Service is selected
const editServiceCheckbox = document.getElementById("edit-category-service");
const editServiceDescription = document.getElementById("service_description");
const editServiceReminderSection = document.getElementById("edit_service_reminder_section");
const countsAsServiceInput = document.getElementById("counts_as_service");

function updateEditServiceDescription() {
    const isService = editServiceCheckbox.checked;

    editServiceDescription.required = isService;
    editServiceReminderSection.hidden = !isService;
    countsAsServiceInput.disabled = !isService;
}

editServiceCheckbox.addEventListener("change", updateEditServiceDescription);
updateEditServiceDescription();

//Start the editor
pageNavigation.hidden = invoicePages.length <= 1;

if (invoicePages.length > 0) {
    loadEditPage();
} else {
    sectionsEditor.hidden = true;
    textFallback.hidden = false;
    syncInvoiceData();
}
