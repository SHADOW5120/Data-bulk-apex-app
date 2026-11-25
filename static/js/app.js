
document.addEventListener("DOMContentLoaded", function () {

    const tableSearch = document.querySelector("#oracle-table-search");
    const tableSelect = document.querySelector("#oracle-table-select");
    const procedureSearch = document.querySelector("#oracle-procedure-search");
    const procedureSelect = document.querySelector("#oracle-procedure-select");

    const targetSelectionForm = document.querySelector("#target-selection-form");
    const importMode = document.querySelector("#import-mode");
    const loadProcedureButton = document.querySelector("#load-procedure-button");
    const procedureLoadError = document.querySelector("#procedure-load-error");

    if (
        targetSelectionForm
        && importMode
        && loadProcedureButton
        && procedureLoadError
    ) {
        targetSelectionForm.addEventListener("submit", async function (event) {
            if (importMode.value !== "PROCEDURE") return;
            event.preventDefault();

            const originalLabel = loadProcedureButton.textContent.trim();
            const controller = new AbortController();
            const timeoutId = window.setTimeout(function () {
                controller.abort();
            }, 60000);

            procedureLoadError.textContent = "";
            loadProcedureButton.disabled = true;
            loadProcedureButton.textContent = "Loading parameters...";
            targetSelectionForm.setAttribute("aria-busy", "true");

            try {
                const response = await fetch(targetSelectionForm.action, {
                    method: "POST",
                    body: new FormData(targetSelectionForm),
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json"
                    },
                    signal: controller.signal
                });
                const contentType = response.headers.get("content-type") || "";
                if (!contentType.includes("application/json")) {
                    throw new Error(
                        "The server returned an unexpected response "
                        + "(HTTP " + response.status + ")."
                    );
                }

                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(
                        result.error || "Could not load procedure parameters."
                    );
                }
                if (!result.redirect_url) {
                    throw new Error("The server did not provide the mapping page URL.");
                }
                window.location.assign(result.redirect_url);
            } catch (error) {
                procedureLoadError.textContent = error.name === "AbortError"
                    ? "Oracle parameter lookup timed out after 60 seconds."
                    : "Could not load procedure parameters: " + error.message;
            } finally {
                window.clearTimeout(timeoutId);
                loadProcedureButton.disabled = false;
                loadProcedureButton.textContent = originalLabel;
                targetSelectionForm.removeAttribute("aria-busy");
            }
        });
    }

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

        if (procedureSearch && procedureSelect) {
            const previousButton = document.querySelector("#oracle-procedure-previous");
            const nextButton = document.querySelector("#oracle-procedure-next");
            const pageLabel = document.querySelector("#oracle-procedure-page");
            const searchError = document.querySelector("#oracle-procedure-search-error");
            let currentPage = 1;
            let hasMore = nextButton && !nextButton.disabled;
            let searchTimer;
            let requestSequence = 0;

            async function loadProcedures(page) {
                const requestId = ++requestSequence;
                const url = new URL(procedureSearch.dataset.searchUrl, window.location.origin);
                url.searchParams.set("q", procedureSearch.value);
                url.searchParams.set("page", page);

                if (searchError) searchError.textContent = "";
                try {
                    const response = await fetch(url);
                    const result = await response.json();
                    if (requestId !== requestSequence) return;
                    if (!response.ok) {
                        throw new Error(result.error || "Procedure search failed.");
                    }

                    procedureSelect.replaceChildren(
                        new Option("Select Oracle Procedure", "")
                    );
                    result.procedures.forEach(function (procedure) {
                        const key = [
                            procedure.package_name || "",
                            procedure.procedure_name,
                            procedure.subprogram_id
                        ].join("|");
                        procedureSelect.add(new Option(procedure.label, key));
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

            procedureSearch.addEventListener("input", function () {
                window.clearTimeout(searchTimer);
                searchTimer = window.setTimeout(function () {
                    loadProcedures(1);
                }, 250);
            });
            if (previousButton) {
                previousButton.addEventListener("click", function () {
                    if (currentPage > 1) loadProcedures(currentPage - 1);
                });
            }
            if (nextButton) {
                nextButton.addEventListener("click", function () {
                    if (hasMore) loadProcedures(currentPage + 1);
                });
            }
        }

        if (importMode) {
            function updateImportMode() {
                const procedureMode = importMode.value === "PROCEDURE";
                document.querySelectorAll(".mapping-target-direct").forEach(function (element) {
                    element.style.display = procedureMode ? "none" : "";
                    element.querySelectorAll("input, select, button").forEach(function (control) {
                        control.disabled = procedureMode;
                    });
                });
                document.querySelectorAll(".mapping-target-procedure").forEach(function (element) {
                    element.style.display = procedureMode ? "" : "none";
                    element.querySelectorAll("input, select, button").forEach(function (control) {
                        control.disabled = !procedureMode;
                    });
                });
                if (procedureMode && procedureSearch && procedureSelect.options.length <= 1) {
                    procedureSearch.dispatchEvent(new Event("input"));
                }
                if (!procedureMode && tableSearch && tableSelect.options.length <= 1) {
                    tableSearch.dispatchEvent(new Event("input"));
                }
            }
            importMode.addEventListener("change", updateImportMode);
            updateImportMode();
        }

        document.querySelectorAll(".procedure-source").forEach(function (select) {
            function updateProcedureSource() {
                const row = select.closest("tr");
                row.querySelectorAll(".procedure-source-field").forEach(function (field) {
                    const source = select.value;
                    const isMetadata = source.startsWith("FILE_");
                    const visible = field.dataset.source === source
                        || (field.dataset.source === "FILE_METADATA" && isMetadata);
                    field.style.display = visible ? "" : "none";
                });
            }
            select.addEventListener("change", updateProcedureSource);
            updateProcedureSource();
        });

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
