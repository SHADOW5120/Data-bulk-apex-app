
document.addEventListener("DOMContentLoaded", function () {

    const tableSearch = document.querySelector("#oracle-table-search");
    const tableSelect = document.querySelector("#oracle-table-select");

    if (tableSearch && tableSelect) {
        const previousButton = document.querySelector("#oracle-table-previous");
        const nextButton = document.querySelector("#oracle-table-next");
        const pageLabel = document.querySelector("#oracle-table-page");
        const searchError = document.querySelector("#oracle-table-search-error");
        let currentPage = 1;
        let hasMore = nextButton && !nextButton.disabled;
        let searchTimer;
        let requestSequence = 0;

        async function loadTables(page) {
            const requestId = ++requestSequence;
            const url = new URL(tableSearch.dataset.searchUrl, window.location.origin);
            url.searchParams.set("q", tableSearch.value);
            url.searchParams.set("page", page);

            if (searchError) searchError.textContent = "";
            try {
                const response = await fetch(url);
                const result = await response.json();
                if (requestId !== requestSequence) return;
                if (!response.ok) {
                    throw new Error(result.error || "Table search failed.");
                }

                tableSelect.replaceChildren(new Option("Select Oracle Table", ""));
                result.tables.forEach(function (table) {
                    tableSelect.add(new Option(table.table_name, table.table_name));
                });
                currentPage = result.page;
                hasMore = result.has_more;
                if (pageLabel) pageLabel.textContent = "Page " + currentPage;
                if (previousButton) previousButton.disabled = currentPage <= 1;
                if (nextButton) nextButton.disabled = !hasMore;
            } catch (error) {
                if (requestId !== requestSequence) return;
                if (searchError) searchError.textContent = error.message;
                if (nextButton) nextButton.disabled = true;
            }
        }

        tableSearch.addEventListener("input", function () {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(function () {
                loadTables(1);
            }, 250);
        });
        if (previousButton) {
            previousButton.addEventListener("click", function () {
                if (currentPage > 1) loadTables(currentPage - 1);
            });
        }
        if (nextButton) {
            nextButton.addEventListener("click", function () {
                if (hasMore) loadTables(currentPage + 1);
            });
        }
    }

    // Keep the Oracle type hint aligned with the selected column.
    document.querySelectorAll(".oracle-column-select").forEach(function (select) {

        function updateMappingType() {
            const row = select.closest("tr");
            const option = select.options[select.selectedIndex];
            const oracleType = option ? option.dataset.type : "";
            const typeLabel = row.querySelector(".oracle-type-label");

            if (typeLabel) {
                typeLabel.textContent = oracleType || "Select target column";
            }
        }

        select.addEventListener("change", updateMappingType);
        updateMappingType();
    });

    // Display selected Excel file
    const excelInput = document.querySelector(
        'input[name="excel_file"]'
    );

    if (excelInput) {

        excelInput.addEventListener("change", function () {

            const file = this.files[0];

            if (!file) return;

            console.log("Excel selected:", file.name);

        });

    }


    // Display selected ZIP file
    const zipInput = document.querySelector(
        'input[name="zip_file"]'
    );

    if (zipInput) {

        zipInput.addEventListener("change", function () {

            const file = this.files[0];

            if (!file) return;

            console.log("ZIP selected:", file.name);

        });

    }


    // Confirm form submissions
    const uploadForms = document.querySelectorAll(
        'form[data-confirm]'
    );

    uploadForms.forEach(function (form) {

        form.addEventListener("submit", function (event) {

            const message = form.dataset.confirm;

            if (!confirm(message)) {
                event.preventDefault();
            }

        });

    });

});
