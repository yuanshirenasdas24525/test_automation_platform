pipeline {

    agent { label 'builder' }

    environment {
        CONTAINER_NAME = "api_test_platform"
        IMAGE_NAME = "test_automation_platform-main-api_test:latest"
        ALLURE_RESULTS = "data/reports/allure-results"
    }

    tools {
        allure 'AllureCommandline'  // 这里填 Jenkins 已配置的 Allure 名称
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Verify Config') {
            steps {
                script {
                    sh """
                        echo "Checking config directory..."
                        ls -l config
                        if [ ! -f config/settings.py ]; then
                            echo "ERROR: settings.py not found"
                            exit 1
                        fi
                    """
                }
            }
        }

        stage('Prepare Image') {
            steps {
                script {
                    sh """
                       echo "Image building..."
                       docker build -t ${IMAGE_NAME} -f docker/Dockerfile .
                    """
                }
            }
        }

        stage('Run Tests') {
            steps {
                script {
                    def allureVolumePath = "${env.WORKSPACE}/data/reports"
                    sh "echo 📁 确认 WORKSPACE: ${allureVolumePath}"

                    def result = sh script: """
                    docker run --rm --name ${CONTAINER_NAME} -u 0:0 \\
                      -e PYTHONUNBUFFERED=1 -e TZ=Asia/Shanghai -e PYTHONPATH=/app \\
                      -e CI=true \\
                      -v ${WORKSPACE}/data/reports:/app/data/reports \\
                      -v ${WORKSPACE}/tests:/app/tests \\
                      -v ${WORKSPACE}/config:/app/config \\
                      -v ${WORKSPACE}/data/api_auto:/app/data/api_auto \\
                      -v ${WORKSPACE}/data/log:/app/data/log \\
                      -w /app ${IMAGE_NAME} \\
                      -t api -c tests/test_api.py

                    echo '🧪 Container finished running. Check generated files:'
                    ls -la ${allureVolumePath}/allure-results || echo "❌ Report folder not created!"
                    """, returnStatus: true

                    if (result != 0) {
                        echo "Docker container exited with code ${result}, but continue..."
                    }
                }
            }
        }

        stage('Allure Report') {
            steps {
                allure([
                    includeProperties: false,
                    jdk: '',
                    results: [[path: 'data/reports/allure-results']]
                ])
            }
        }
    }

    post {
        always {
            sh "echo '✅ Pipeline finished!' || true"
        }
    }
}