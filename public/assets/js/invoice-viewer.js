
const invoiceId = Number(document.body.dataset.invoiceId);

const savedInvoiceData = JSON.parse(
    document.getElementById("saved_invoice_data").textContent || "{}"
);

//Store the individual invoice pages
const invoicePages = savedInvoiceData.pages || [];

const totalPages = Number(document.body.dataset.totalPages);
let currentPage = 0;

const previewImage = document.getElementById("invoice_preview_image");
const zoomImage = document.getElementById("invoice_zoom_image");
const previousButton = document.getElementById("previous_page");
const nextButton = document.getElementById("next_page");
const pageCounter = document.getElementById("page_counter");
const invoiceContent = document.getElementById("invoice_page_content");
const notesSection = document.getElementById("invoice_notes_section");
const pageNotes = document.getElementById("invoice_page_notes");

//Display tables and notes in their saved order
function showPageContent() {
    const page = invoicePages[currentPage];
    if (!page) return;

    invoiceContent.replaceChildren();

    let blocks = page.blocks;

    //Support older invoices without blocks
    if (!Array.isArray(blocks)) {
        blocks = [];

        if (page.table?.rows?.length) {
            blocks.push({
                type: "table",
                columns: page.table.columns || [],
                rows: page.table.rows
            });
        }

        (page.sections || []).forEach(function (section) {
            blocks.push({
                type: "text",
                heading: section.heading,
                content: section.content
            });
        });
    }

    blocks.forEach(function (block) {
        const section = document.createElement("div");
        section.className = "mt-4";

        if (block.heading) {
            const heading = document.createElement("h4");
            heading.className = "mb-2";
            heading.style.color = "#0d6efd";
            heading.textContent = block.heading;
            section.appendChild(heading);
        }

        if (block.type === "table") {
            const columns = block.columns || [];
            const rows = block.rows || [];
            const labels = block.column_labels || {};

            if (!columns.length) return;

            const wrapper = document.createElement("div");
            wrapper.className = "table-responsive";

            const table = document.createElement("table");
            table.className = "table table-bordered align-middle";

            const thead = document.createElement("thead");
            const headerRow = document.createElement("tr");

            columns.forEach(function (column) {
                const th = document.createElement("th");

                th.textContent = Object.prototype.hasOwnProperty.call(labels, column)
                    ? labels[column]
                    : column.replaceAll("_", " ");

                headerRow.appendChild(th);
            });

            thead.appendChild(headerRow);
            table.appendChild(thead);

            const tbody = document.createElement("tbody");

            rows.forEach(function (row) {
                const tr = document.createElement("tr");

                columns.forEach(function (column) {
                    const td = document.createElement("td");
                    td.textContent = row[column] ?? "";
                    tr.appendChild(td);
                });

                tbody.appendChild(tr);
            });

            table.appendChild(tbody);
            wrapper.appendChild(table);
            section.appendChild(wrapper);

        } else if (block.type === "text") {
            const content = document.createElement("p");
            content.style.whiteSpace = "pre-wrap";
            content.style.lineHeight = "1.7";
            content.textContent = String(block.content || "").replace(/\\n/g, "\n");
            section.appendChild(content);
        }

        invoiceContent.appendChild(section);
    });

    //Show OCR text if no tables or sections exist
    if (!blocks.length) {
        const text = document.createElement("div");
        text.className = "invoice-ocr-content";
        text.style.whiteSpace = "pre-wrap";
        text.textContent = page.content_text ?? page.raw_text ?? "No invoice content detected.";
        invoiceContent.appendChild(text);
    }
}

//Display notes for the selected invoice page
function showPageNotes() {
    const page = invoicePages[currentPage];

    //Use saved sections instead of the old notes field
    if (page && (
        (page.blocks && page.blocks.length > 0) ||
        (page.sections && page.sections.length > 0)
    )) {
        pageNotes.replaceChildren();
        notesSection.hidden = true;
        return;
    }

    pageNotes.replaceChildren();

    if (!page) {
        notesSection.hidden = true;
        return;
    }

    const notes = (page.notes ?? "").trim();
    const content = page.content_text ?? page.raw_text ?? "";

    const notesLines = notes
        .split("\n")
        .map(line => line.trim())
        .filter(Boolean);

    const contentLines = new Set(
        content.split("\n").map(line => line.trim())
    );

    const notesAlreadyShown =
        !(page.table?.rows?.length > 0) &&
        notesLines.length > 0 &&
        notesLines.every(line => contentLines.has(line));

    //Avoid displaying empty or duplicated notes
    if (!notes || notesAlreadyShown) {
        notesSection.hidden = true;
        return;
    }

    notesLines.forEach(function (line) {
        const paragraph = document.createElement("p");
        paragraph.textContent = line;
        pageNotes.appendChild(paragraph);
    });

    notesSection.hidden = false;
}

//Display the selected invoice page
function showInvoicePage() {
    //Load the smaller WebP preview
    const previewUrl = `/invoice/${invoiceId}/preview/${currentPage}`;

    if (previewImage) {
        previewImage.src = previewUrl;
    }

    //Only load the full image when the popup opens
    if (zoomImage) {
        zoomImage.removeAttribute("src");
    }

    if (pageCounter) {
        pageCounter.textContent = `Page ${currentPage + 1} of ${totalPages}`;
    }

    if (previousButton) {
        previousButton.disabled = currentPage === 0;
    }

    if (nextButton) {
        nextButton.disabled = currentPage === totalPages - 1;
    }

    showPageContent();
    showPageNotes();
}

//Navigate between invoice pages
if (totalPages > 1 && previousButton && nextButton) {
    previousButton.addEventListener("click", function () {
        if (currentPage > 0) {
            currentPage--;
            showInvoicePage();
        }
    });

    nextButton.addEventListener("click", function () {
        if (currentPage < totalPages - 1) {
            currentPage++;
            showInvoicePage();
        }
    });
}

//Full-resolution invoice image popup
const imageModal = document.getElementById("invoiceImageModal");

if (imageModal && zoomImage) {
    imageModal.addEventListener("show.bs.modal", function () {
        zoomImage.src = `/invoice/${invoiceId}/image/${currentPage}`;
    });

    imageModal.addEventListener("hidden.bs.modal", function () {
        zoomImage.removeAttribute("src");
    });
}

//Display the first saved invoice page
if (invoicePages.length > 0) {
    showPageContent();
    showPageNotes();
}
