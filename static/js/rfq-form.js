const itemsContainer = document.querySelector("#itemsContainer");
const addItemButton = document.querySelector("#addItem");
const totalFormsInput = document.querySelector("#id_items-TOTAL_FORMS");
const itemTemplate = document.querySelector("#emptyItemTemplate");

function bindRemoveButton(row) {
    row.querySelector(".remove-item")?.addEventListener("click", () => {
        const deleteInput = row.querySelector('input[name$="-DELETE"]');
        if (deleteInput && row.querySelector('input[name$="-id"]')?.value) {
            deleteInput.checked = true;
            row.classList.add("hidden");
        } else {
            row.remove();
        }
    });
}

document.querySelectorAll(".item-row").forEach(bindRemoveButton);

addItemButton?.addEventListener("click", () => {
    const index = Number(totalFormsInput.value);
    const wrapper = document.createElement("div");
    wrapper.innerHTML = itemTemplate.innerHTML.replaceAll("__prefix__", index).trim();
    const newRow = wrapper.firstElementChild;
    itemsContainer.appendChild(newRow);
    totalFormsInput.value = index + 1;
    bindRemoveButton(newRow);
});
