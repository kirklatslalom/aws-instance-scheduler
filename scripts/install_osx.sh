#!/bin/bash

DIR=/opt/homebrew/bin/
PACKAGES=(
    "pylint"
    "ansible-lint"
    "ansible"
    "pre-commit"
    "black"
    "terraform-docs"
    "tflint"
    "coreutils"
    "gawk"
    "tfsec"
    "hadolint"
    "jq"
)

# Install prerequisites
if [[ "$OSTYPE" == "darwin"* ]]; then
    for pkg in "${PACKAGES[@]}"; do
        if [ ! -f "${DIR}/${pkg}" ]; then
            brew install "${pkg}"
        fi
    done

    if [ ! -f "${DIR}/terraform" ]; then
        brew install tfenv
        tfenv init
        LATEST=`tfenv list-remote | head -1`
        tfenv install "${LATEST}"
        tfenv use "${LATEST}"
    fi

    if [ ! -f "${DIR}/infracost" ]; then
        brew install infracost
    fi
fi

python3 -m pip install -r requirements.txt

if [ $? -eq 0 ] && [ -f .git/hooks/pre-commit ]; then
    pre-commit uninstall
    pre-commit install
fi

DIR=~/.git-template
git config --global init.templateDir "${DIR}"
pre-commit init-templatedir -t pre-commit "${DIR}"
