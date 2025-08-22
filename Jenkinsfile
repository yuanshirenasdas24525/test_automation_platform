pipeline {

    agent { label 'builder' }

    environment {
        CONTAINER_NAME = "api_test_platform"
        IMAGE_NAME = "test_automation_platform-main-api_test:latest"
        ALLURE_RESULTS = "data/reports/allure-results"
    }

    tools {
        allure 'AllureCommandline'  // 這裡填你設定的 Allure 安裝名稱
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
                       echo "Image  building..."
                       docker build -t ${IMAGE_NAME} -f docker/Dockerfile . 
                       """
                }
            }
        }

        stage('Run Tests') {
            steps {
                script {
                    def allureVolumePath = "${env.WORKSPACE}/data/reports"
                    sh "echo 📁 確認 WORKSPACE: ${allureVolumePath}"
            
                    def result = sh script: '''
                    docker run --rm --name api_test_platform -u 0:0 \
                      -e PYTHONUNBUFFERED=1 -e TZ=Asia/Shanghai -e PYTHONPATH=/app \
                      -v ${WORKSPACE}/data/reports:/app/data/reports \
                      -w /app test_automation_platform-main-api_test:latest \
                      -t api -c tests/test_api.py --alluredir /app/data/reports/allure-results

                    echo '🧪 Container finished running. Check generated files:'
                    ls -la ${allureVolumePath}/allure-results || echo "❌ Report folder not created!"
                    ''', returnStatus: true

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
            sh "echo 'good job' || true"
        }
    }
}
