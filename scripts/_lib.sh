run_python_on_web(){
    USAGE_MSG="$1"
    shift
    usage(){
        echo "$USAGE_MSG" >&2
        echo "exit 1"
        exit 1
    }

    if [ "$#" = 0 ]; then usage; fi

    case "$1" in
        k8s)
            if [ "$#" != 2 ]; then usage; exit 1; fi
            TIER="$2"
            POD=$(kubectl get pod \
                -l "repro-pod=web,tier=$TIER" \
                -o jsonpath='{.items[0].metadata.name}' | head -n 1)
            echo "kubectl exec -i \"$POD\" python"
        ;;
        docker)
            echo 'docker exec -i reproserver-web python'
        ;;
        *)
            usage
        ;;
    esac
}
